"""
카카오톡 나에게 보내기 API
https://developers.kakao.com/docs/latest/ko/message/rest-api
"""
import json
import logging
import os
from pathlib import Path

import requests

from config import (
    KAKAO_REST_API_KEY, KAKAO_CLIENT_SECRET,
    KAKAO_ACCESS_TOKEN, KAKAO_REFRESH_TOKEN, KAKAO_TOKEN_PATH,
)

logger = logging.getLogger(__name__)

KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


def _load_tokens() -> dict:
    if KAKAO_TOKEN_PATH.exists():
        try:
            return json.loads(KAKAO_TOKEN_PATH.read_text())
        except Exception:
            pass
    return {
        "access_token": KAKAO_ACCESS_TOKEN,
        "refresh_token": KAKAO_REFRESH_TOKEN,
    }


def _save_tokens(tokens: dict):
    KAKAO_TOKEN_PATH.write_text(json.dumps(tokens))
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # GitHub Actions: 다음 워크플로우 단계에서 gh secret set으로 반영
        Path("/tmp/kakao_tokens_new.json").write_text(
            json.dumps(tokens, ensure_ascii=False)
        )
        return
    # 로컬: .env 파일 직접 업데이트
    _update_env("KAKAO_ACCESS_TOKEN", tokens["access_token"])
    if tokens.get("refresh_token"):
        _update_env("KAKAO_REFRESH_TOKEN", tokens["refresh_token"])


def _update_env(key: str, value: str):
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    content = env_path.read_text()
    pattern = f"{key}="
    lines = content.splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(pattern):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


def _refresh_access_token(refresh_token: str) -> dict | None:
    data = {
        "grant_type": "refresh_token",
        "client_id": KAKAO_REST_API_KEY,
        "refresh_token": refresh_token,
    }
    if KAKAO_CLIENT_SECRET:
        data["client_secret"] = KAKAO_CLIENT_SECRET
    try:
        resp = requests.post(KAKAO_TOKEN_URL, data=data, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("카카오 토큰 갱신 실패: %s", e)
        return None


def _get_access_token() -> str:
    tokens = _load_tokens()
    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")

    if not access_token:
        raise ValueError("KAKAO_ACCESS_TOKEN이 설정되지 않았습니다. setup.py --kakao-auth 실행")

    return access_token, refresh_token


def send_message(text: str) -> bool:
    """카카오톡 나에게 텍스트 메시지 전송"""
    access_token, refresh_token = _get_access_token()

    template = {
        "object_type": "text",
        "text": text[:1000],  # 카카오 제한: 200자 (링크 포함 시 최대 1000)
        "link": {
            "web_url": "https://finance.naver.com",
            "mobile_web_url": "https://m.finance.naver.com",
        },
        "button_title": "네이버 증권",
    }

    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps(template, ensure_ascii=False)}

    resp = requests.post(KAKAO_SEND_URL, headers=headers, data=data, timeout=10)

    # 토큰 만료 시 갱신 후 재시도
    if resp.status_code == 401 and refresh_token:
        logger.info("카카오 토큰 만료 - 갱신 시도")
        new_tokens = _refresh_access_token(refresh_token)
        if new_tokens:
            _save_tokens({
                "access_token": new_tokens["access_token"],
                "refresh_token": new_tokens.get("refresh_token", refresh_token),
            })
            headers["Authorization"] = f"Bearer {new_tokens['access_token']}"
            resp = requests.post(KAKAO_SEND_URL, headers=headers, data=data, timeout=10)

    if resp.status_code == 200:
        logger.info("카카오톡 전송 성공")
        return True
    else:
        logger.error("카카오톡 전송 실패: %s %s", resp.status_code, resp.text)
        return False


def send_briefing_messages(messages: list[str]) -> bool:
    """여러 메시지를 순서대로 전송 (카카오 200자 제한 대응)"""
    success = True
    for msg in messages:
        if not send_message(msg):
            success = False
    return success
