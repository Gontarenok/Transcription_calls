from datetime import datetime

from db.base import SessionLocal
from db.crud import (
    add_transcription,
    create_or_get_call,
    get_or_create_call_type,
    get_or_create_user,
)


def run():
    db = SessionLocal()

    try:
        # # Справочник типов звонков
        # call_type_911 = get_or_create_call_type(
        #     db,
        #     code="911",
        #     name="Внутренняя техническая поддержка",
        #     description="Звонки внутренней технической поддержки",
        # )
        # call_type_kc = get_or_create_call_type(
        #     db,
        #     code="KЦ",
        #     name="Контакт-центр",
        #     description="Звонки в контакт-центр компании",
        # )
        #
        # # Тестовый менеджер
        # user = get_or_create_user(
        #     db,
        #     manager_folder="manager_001",
        #     full_name="Тестовый Менеджер",
        #     domain="test.manager",
        #     department="Contact Center",
        # )
        #
        # # Пример звонка 911
        # call_911 = create_or_get_call(
        #     db,
        #     manager_id=user.id,
        #     call_type_id=call_type_911.id,
        #     file_name="911_record_001.mp3",
        #     source_file_path=r"C:\Audio_share\TechSupport\2026-01-01\manager_001\911_record_001.mp3",
        #     call_started_at=datetime.now(),
        #     octell_call_id="oct_911_001",
        #     duration_seconds=80.5,
        #     status="NEW",
        # )
        #
        # # Пример звонка КЦ
        # call_kc = create_or_get_call(
        #     db,
        #     manager_id=user.id,
        #     call_type_id=call_type_kc.id,
        #     file_name="kc_record_001.mp3",
        #     source_file_path=r"C:\Audio_share\Contact_center\2026-01-01\manager_001\kc_record_001.mp3",
        #     call_started_at=datetime.now(),
        #     octell_call_id="oct_kc_001",
        #     duration_seconds=123.4,
        #     status="NEW",
        # )
        #
        # tr = add_transcription(
        #     db,
        #     call_id=call_kc.id,
        #     model_name="whisper-large-v3",
        #     text="Привет, это тестовая транскрибация КЦ",
        # )

        # Системный менеджер для звонков 911 (без разбивки по папкам менеджеров)
        manager_911 = get_or_create_user(
            db,
            manager_folder="manager_911_system",
            full_name="Менеджер 911",
            domain="911.system",
            department="911",
        )

        # print("CallType 911:", call_type_911.id, call_type_911.code, call_type_911.name)
        # print("CallType KЦ:", call_type_kc.id, call_type_kc.code, call_type_kc.name)
        # print("Call 911:", call_911.id, call_911.file_name)
        # print("Call KC:", call_kc.id, call_kc.file_name)
        # print("Transcription:", tr.id, tr.model_name)
        print("Manager 911:", manager_911.id, manager_911.full_name)

    finally:
        db.close()


if __name__ == "__main__":
    run()