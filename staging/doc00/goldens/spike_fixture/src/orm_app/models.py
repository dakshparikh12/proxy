"""ORM models — the spike's get_dependents / who_writes target module."""


class User:
    __tablename__ = "users"

    def __init__(self, uid: str, email: str) -> None:
        self.uid = uid
        self.email = email
