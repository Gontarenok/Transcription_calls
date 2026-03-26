from db.base import engine, Base
from db import models  # важно: чтобы модели зарегистрировались


def create_all():
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("Done.")


def drop_all():
    print("Dropping tables...")
    Base.metadata.drop_all(bind=engine)
    print("Done.")


if __name__ == "__main__":
    create_all()