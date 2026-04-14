import os
import base64
import requests
from dotenv import load_dotenv
import json

from upload_work import TASK_TEXT

load_dotenv()

BASE_URL = os.getenv("WORK_PATH")
reports_dir = "output_reports"
texts_dir = "output_task_texts"

# 👤 данные для логина
USERNAME = os.getenv("WORK_USER")
PASSWORD = os.getenv("WORK_PASS")

# ⚙️ параметры задачи
SUBCAT_ID = 1501
TASK_TITLE = "Отчет по звонкам"
TASK_TEXT = "ТЕСТ! Ежедневный отчет по звонкам. Таблица с транскрибацией и саммари звонков во вложении."
PRIORITY_ID = 1
OWNER_USER_ID = 5660  # Заказчик задачи
PERFORMER_USER_ID = 6572  # Исполнитель
SUBSCRIBER_IDS = [27231, 20757]  # Подписчики
NOTIFY_IDS = [27231, 20757]

def get_latest_report_file():
    """Берём последний по времени Excel-отчёт"""
    return max(
        (os.path.join(reports_dir, f) for f in os.listdir(reports_dir) if f.endswith(".xlsx")),
        key=os.path.getctime
    )


def get_latest_task_text():
    """Берём содержимое последнего текстового отчёта"""
    latest_txt = max(
        (os.path.join(texts_dir, f) for f in os.listdir(texts_dir) if f.endswith(".txt")),
        key=os.path.getctime
    )
    with open(latest_txt, "r", encoding="utf-8") as f:
        text = f.read()
    return text

def log_request_response(response):
    print("----- 🔽 REQUEST -----")
    req = response.request
    print("URL:", req.url)
    print("Method:", req.method)
    print("Headers:", dict(req.headers))
    if req.body:
        try:
            print("Body:", json.loads(req.body))
        except Exception:
            print("Body (raw):", req.body)

    print("----- 🔼 RESPONSE -----")
    print("Status:", response.status_code)
    print("Headers:", dict(response.headers))
    print("Text:", response.text)
    print("----------------------")


def get_1forma_token():
    """Получение токена 1formaAuth"""
    params = {
        "login": USERNAME,
        "password": PASSWORD,
        "isPersistent": "true"
    }
    resp = requests.get(f"{BASE_URL}/app/v1.0/api/auth/token", params=params)
    print("🔐 Авторизация:", resp.status_code)
    resp.raise_for_status()
    token = resp.headers.get("1FormaAuth")
    print("✅ Токен получен")
    return token


def create_task(token, task_text):
    """Создание задачи"""
    url = f"{BASE_URL}/api/tasks/create"
    headers = {"Cookie": f"1FormaAuth={token}"}

    payload = {
        "subcatId": SUBCAT_ID,
        # "title": TASK_TITLE,
        "taskText": task_text,
        "performerIds": [PERFORMER_USER_ID],
        "orderedTime": None,
        "priorityId": PRIORITY_ID,
        "userToMakeOwnerId": OWNER_USER_ID,
        "addToFavourites": False,
        "extParams": [
            {"id": 2382,        # дерево сервисов
             "value": 6314792
             }],
        "subscriberIds": SUBSCRIBER_IDS,
        "notifyIds": NOTIFY_IDS,
        "parentTaskId": None,
        "linkedTaskId": None
    }

    response = requests.post(url, json=payload, headers=headers)
    log_request_response(response)
    print("🚀 Создание задачи:", response.status_code)
    response.raise_for_status()

    result = response.json()
    task_id = result["data"]["value"]  # ✅ здесь твоя логика
    print(f"✅ Задача создана! ID = {task_id}")
    task_url = f"https://work.metallprofil.ru/spa/tasks/{task_id}"
    print(f"🔗 Ссылка: {task_url}")
    return task_id


def preupload_file_base64(file_path, token):
    """Загрузка файла во временное хранилище"""
    url = f"{BASE_URL}/api/files/preupload/base64"
    headers = {"Cookie": f"1FormaAuth={token}"}

    with open(file_path, "rb") as f:
        file_data = f.read()
        file_data_base64 = base64.b64encode(file_data).decode("utf-8")

    payload = {
        "fileName": os.path.basename(file_path),
        "fileData": file_data_base64,
        "writeComment": False,
        "comment": ""
    }

    response = requests.post(url, json=payload, headers=headers)
    print("📤 Preupload:", response.status_code)
    response.raise_for_status()

    result = response.json()
    pre_upload_id = result["data"][0]["preUploadFileId"]
    print(f"✅ Файл загружен в промежуточное хранилище. preUploadFileId = {pre_upload_id}")
    return pre_upload_id


def attach_file_to_task(task_id, pre_upload_id, token, initiator_id):
    """Привязка файла к задаче по документации"""
    url = f"{BASE_URL}/api/files/upload/PreUploadedFilesToTask?initiatorUserId={initiator_id}"
    headers = {"Cookie": f"1FormaAuth={token}"}

    payload = {
        "taskId": task_id,
        "preUploadedFileId": pre_upload_id,
    }

    response = requests.post(url, json=payload, headers=headers)
    print("📎 Привязка файла:", response.status_code)
    print("Ответ:", response.text)
    response.raise_for_status()

    result = response.json()
    print(f"✅ Файл успешно прикреплён к задаче {task_id}")
    return result


# --- ОСНОВНОЙ БЛОК ---
if __name__ == "__main__":
    token = get_1forma_token()

    # Берём текст отчета
    task_text = get_latest_task_text()

    # Создаём задачу
    task_id = create_task(token, task_text)

    # Прикрепляем последний Excel
    if task_id:
        report_file = get_latest_report_file()
        pre_id = preupload_file_base64(report_file, token)
        if pre_id:
            attach_file_to_task(task_id, pre_id, token, OWNER_USER_ID)