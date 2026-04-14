import os
import pandas as pd
from datetime import datetime

reports_dir = "output_reports"
summaries_dir = "output_summary"
texts_dir = "output_task_texts"
os.makedirs(texts_dir, exist_ok=True)


def normalize_status(status: str) -> str:
    """Приводим разные формулировки к трём категориям + Не указано"""
    if not isinstance(status, str):
        return "Не указано"

    s = status.lower()
    if "не помогли" in s:
        return "Не помогли"
    elif "помогли" in s:
        return "Помогли"
    elif "работ" in s:
        return "В работе"
    else:
        return "Не указано"


def build_task_text():
    # 1. Определяем последний отчёт (Excel)
    latest_report = max(
        (os.path.join(reports_dir, f) for f in os.listdir(reports_dir) if f.endswith(".xlsx")),
        key=os.path.getctime
    )

    # 2. Определяем последнюю папку в summary (по дате создания)
    latest_summary_dir = max(
        (os.path.join(summaries_dir, d) for d in os.listdir(summaries_dir) if os.path.isdir(os.path.join(summaries_dir, d))),
        key=os.path.getctime
    )
    calls_count = len([f for f in os.listdir(latest_summary_dir) if f.endswith(".txt")])

    # 3. Загружаем Excel и нормализуем статусы
    df = pd.read_excel(latest_report)
    df["НормСтатус"] = df["Итог"].apply(normalize_status)
    status_counts = df["НормСтатус"].value_counts().to_dict()

    # 4. Формируем текст задачи
    report_date = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"📅 Дата отчёта: {report_date}",
        f"Обработано звонков: {calls_count}"
    ]
    for status in ["Помогли", "Не помогли", "В работе", "Не указано"]:
        lines.append(f"{status}: {status_counts.get(status, 0)}")

    task_text = "\n".join(lines)

    # 5. Сохраняем в текстовый файл
    out_file = os.path.join(texts_dir, f"task_text_{report_date}.txt")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(task_text)

    print(f"✅ Отчёт сформирован: {out_file}")
    print(task_text)

    return task_text, out_file


if __name__ == "__main__":
    build_task_text()
