"""Создание задачи в Work с текстом и вложением Excel (переменные окружения)."""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_1forma_token(base_url: str, username: str, password: str) -> str:
    params = {"login": username, "password": password, "isPersistent": "true"}
    resp = requests.get(f"{base_url.rstrip('/')}/app/v1.0/api/auth/token", params=params, timeout=120)
    resp.raise_for_status()
    token = resp.headers.get("1FormaAuth")
    if not token:
        raise RuntimeError("Ответ авторизации Work без заголовка 1FormaAuth")
    return token


def create_task(
    *,
    base_url: str,
    token: str,
    task_text: str,
    subcat_id: int,
    priority_id: int,
    owner_user_id: int,
    performer_user_ids: list[int],
    subscriber_ids: list[int],
    notify_ids: list[int],
    ext_params: list[dict[str, Any]] | None = None,
) -> int:
    url = f"{base_url.rstrip('/')}/api/tasks/create"
    headers = {"Cookie": f"1FormaAuth={token}"}
    payload: dict[str, Any] = {
        "subcatId": subcat_id,
        "taskText": task_text,
        "performerIds": performer_user_ids,
        "orderedTime": None,
        "priorityId": priority_id,
        "userToMakeOwnerId": owner_user_id,
        "addToFavourites": False,
        "extParams": ext_params or [],
        "subscriberIds": subscriber_ids,
        "notifyIds": notify_ids,
        "parentTaskId": None,
        "linkedTaskId": None,
    }
    response = requests.post(url, json=payload, headers=headers, timeout=120)
    response.raise_for_status()
    result = response.json()
    return int(result["data"]["value"])


def preupload_file_base64(base_url: str, token: str, file_path: str | Path) -> str:
    path = Path(file_path)
    url = f"{base_url.rstrip('/')}/api/files/preupload/base64"
    headers = {"Cookie": f"1FormaAuth={token}"}
    data_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    payload = {
        "fileName": path.name,
        "fileData": data_b64,
        "writeComment": False,
        "comment": "",
    }
    response = requests.post(url, json=payload, headers=headers, timeout=300)
    response.raise_for_status()
    result = response.json()
    return str(result["data"][0]["preUploadFileId"])


def attach_file_to_task(base_url: str, token: str, *, task_id: int, pre_upload_id: str, initiator_user_id: int) -> None:
    url = (
        f"{base_url.rstrip('/')}/api/files/upload/PreUploadedFilesToTask"
        f"?initiatorUserId={initiator_user_id}"
    )
    headers = {"Cookie": f"1FormaAuth={token}"}
    payload = {"taskId": task_id, "preUploadedFileId": pre_upload_id}
    response = requests.post(url, json=payload, headers=headers, timeout=120)
    response.raise_for_status()


def upload_weekly_911_task(*, task_text: str, excel_path: str | Path) -> int:
    """
    Читает WORK_PATH, WORK_USER, WORK_PASS и опциональные WORK_SUBCAT_ID, WORK_PRIORITY_ID,
    WORK_OWNER_USER_ID, WORK_PERFORMER_USER_IDS (через запятую), WORK_SUBSCRIBER_IDS, WORK_NOTIFY_IDS,
    WORK_EXT_PARAMS_JSON (массив JSON для extParams).
    """
    base_url = (os.getenv("WORK_PATH") or "").strip().rstrip("/")
    user = (os.getenv("WORK_USER") or "").strip()
    password = (os.getenv("WORK_PASS") or "").strip()
    if not base_url or not user or not password:
        raise RuntimeError("Задайте WORK_PATH, WORK_USER, WORK_PASS для отправки в Work")

    subcat_id = _env_int("WORK_SUBCAT_ID", 1501) or 1501
    priority_id = _env_int("WORK_PRIORITY_ID", 1) or 1
    owner_user_id = _env_int("WORK_OWNER_USER_ID", 5660) or 5660
    performer_raw = (os.getenv("WORK_PERFORMER_USER_IDS") or "6572").strip()
    performer_user_ids = [int(x.strip()) for x in performer_raw.split(",") if x.strip()]
    sub_raw = (os.getenv("WORK_SUBSCRIBER_IDS") or "27231,20757").strip()
    subscriber_ids = [int(x.strip()) for x in sub_raw.split(",") if x.strip()]
    notify_raw = (os.getenv("WORK_NOTIFY_IDS") or sub_raw).strip()
    notify_ids = [int(x.strip()) for x in notify_raw.split(",") if x.strip()]
    ext_params = None
    ext_json = (os.getenv("WORK_EXT_PARAMS_JSON") or "").strip()
    if ext_json:
        ext_params = json.loads(ext_json)

    token = get_1forma_token(base_url, user, password)
    task_id = create_task(
        base_url=base_url,
        token=token,
        task_text=task_text,
        subcat_id=subcat_id,
        priority_id=priority_id,
        owner_user_id=owner_user_id,
        performer_user_ids=performer_user_ids,
        subscriber_ids=subscriber_ids,
        notify_ids=notify_ids,
        ext_params=ext_params,
    )
    pre_id = preupload_file_base64(base_url, token, excel_path)
    attach_file_to_task(base_url, token, task_id=task_id, pre_upload_id=pre_id, initiator_user_id=owner_user_id)
    log.info("Work task %s created with attachment %s", task_id, excel_path)
    return task_id
