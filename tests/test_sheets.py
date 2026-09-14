from app.sheets import SheetsRepository


class FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeSpreadsheets:
    def __init__(self, titles):
        self.titles = titles

    def get(self, **kwargs):
        return FakeExecute(
            {"sheets": [{"properties": {"title": title}} for title in self.titles]}
        )


class FakeService:
    def __init__(self, titles):
        self.resource = FakeSpreadsheets(titles)

    def spreadsheets(self):
        return self.resource


def test_find_student_uses_sheet_title_not_student_list() -> None:
    repository = SheetsRepository(
        FakeService(["Список студентов", "Иванов Иван Иванович", "Дашборд куратора"]),
        "spreadsheet-id",
    )

    student = repository.find_student("  Иванов\u00a0Иван\u00a0Иванович ")

    assert student is not None
    assert student.display_name == "Иванов Иван Иванович"
    assert student.sheet_name == "Иванов Иван Иванович"


def test_find_student_accepts_unique_part_of_sheet_title() -> None:
    repository = SheetsRepository(FakeService(["Петров Пётр Сергеевич"]), "spreadsheet-id")

    student = repository.find_student("Петров Петр")

    assert student is not None
    assert student.sheet_name == "Петров Пётр Сергеевич"


def test_find_student_rejects_ambiguous_part_of_sheet_title() -> None:
    repository = SheetsRepository(
        FakeService(["Смирнов Сергей", "Смирнов Сергей Андреевич"]),
        "spreadsheet-id",
    )

    assert repository.find_student("Смирнов Сер") is None
