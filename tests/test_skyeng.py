from app.skyeng import ScheduleEvent, parse_weekly_response, render_schedule


def test_parse_weekly_response_reads_current_avatar_timetable() -> None:
    events = parse_weekly_response(
        {
            "timezone": "Europe/Moscow",
            "days": [
                {
                    "date": "2026-09-14",
                    "slots": [
                        {
                            "activityType": "lesson",
                            "subject": "python",
                            "title": "Python",
                            "context": {"expectedLesson": {"title": "Оператор if."}},
                            "startsAt": "2026-09-14T09:30:00+03:00",
                            "endsAt": "2026-09-14T10:15:00+03:00",
                        }
                    ],
                }
            ],
        }
    )

    assert len(events) == 1
    assert events[0].title == "Оператор if."
    assert events[0].program == "Python"
    assert events[0].start == "09:30"
    assert events[0].end == "10:15"


def test_parse_weekly_response_reads_lessons_and_live_events() -> None:
    events = parse_weekly_response(
        {
            "programs": [
                {
                    "programTitle": "Python-разработка",
                    "streamTitle": "Python",
                    "streamTasks": [
                        {
                            "title": "Практика",
                            "type": "practice",
                            "availableAt": "2026-09-14T13:15:00",
                            "deadlineAt": "2026-09-14T14:50:00",
                        }
                    ],
                }
            ],
            "lives": [
                {
                    "title": "Soft skills",
                    "type": "webinar",
                    "startAt": "2026-09-15T09:30:00",
                    "endAt": "2026-09-15T10:15:00",
                }
            ],
        }
    )

    assert events[0].title == "Практика"
    assert events[0].date == "2026-09-14"
    assert events[0].start == "13:15"
    assert events[1].event_type == "webinar"
    rendered = render_schedule(events)
    assert "14.09 (Пн):" in rendered
    assert "13:15–14:50 — Python-разработка: Практика [практика; выполнить на платформе]" in rendered
    assert "РАСПИСАНИЕ SKYENG — ОНЛАЙН-ПЛАТФОРМА" in rendered
    assert "онлайн-созвоны внутри Skyeng" in rendered


def test_parse_weekly_response_deduplicates_events() -> None:
    payload = {
        "programs": [
            {
                "streamTasks": [
                    {
                        "title": "Python",
                        "availableAt": "2026-09-14T09:30:00",
                        "deadlineAt": "2026-09-14T10:15:00",
                    },
                    {
                        "title": "Python",
                        "availableAt": "2026-09-14T09:30:00",
                        "deadlineAt": "2026-09-14T10:15:00",
                    },
                ]
            }
        ]
    }

    assert len(parse_weekly_response(payload)) == 1


def test_render_schedule_distinguishes_same_title_by_activity_type() -> None:
    rendered = render_schedule(
        [
            ScheduleEvent("2026-09-16", "13:15", "14:50", "10.1 Продвинутый Git", "lesson", "Python"),
            ScheduleEvent("2026-09-16", "14:55", "16:30", "10.1 Продвинутый Git", "practice", "Python"),
        ]
    )

    assert "[урок; выполнить на платформе]" in rendered
    assert "[практика; выполнить на платформе]" in rendered
    assert "онлайн-созвон на платформе" not in rendered
