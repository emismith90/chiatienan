from app.teams_parse import parse_activity, strip_mentions

BOT_ID = "28:app-guid-123"


def test_strip_bot_mention_and_collect_people():
    text = "<at>chiatienan</at> 840k cả nhóm trừ <at>An</at>"
    entities = [
        {"type": "mention", "text": "<at>chiatienan</at>", "mentioned": {"id": BOT_ID, "name": "chiatienan"}},
        {"type": "mention", "text": "<at>An</at>", "mentioned": {"id": "29:an", "name": "An"}},
    ]
    clean, bot_mentioned, people = strip_mentions(
        text, entities, bot_app_id="app-guid-123", bot_handle="chiatienan"
    )
    assert bot_mentioned is True
    assert clean == "840k cả nhóm trừ"
    assert people == [{"teams_user_id": "29:an", "name": "An"}]


def test_bot_mention_by_handle_when_id_differs():
    entities = [
        {"type": "mention", "text": "<at>chiatienan</at>", "mentioned": {"id": "x", "name": "chiatienan"}}
    ]
    _, bot_mentioned, _ = strip_mentions(
        "<at>chiatienan</at> hi", entities, bot_app_id="unrelated", bot_handle="chiatienan"
    )
    assert bot_mentioned is True


def test_parse_activity_full():
    activity = {
        "id": "act-1",
        "type": "message",
        "text": "<at>chiatienan</at> 200k An và Bình",
        "from": {"id": "29:sender", "aadObjectId": "aad-x", "name": "Sender"},
        "entities": [
            {"type": "mention", "text": "<at>chiatienan</at>", "mentioned": {"id": BOT_ID, "name": "chiatienan"}}
        ],
        "attachments": [
            {"contentType": "image/png", "contentUrl": "https://x/y.png", "name": "bill.png"},
            {"contentType": "application/vnd.microsoft.teams.file.download.info", "name": "f"},
        ],
    }
    parsed = parse_activity(activity, bot_app_id="app-guid-123", bot_handle="chiatienan")
    assert parsed.activity_id == "act-1"
    assert parsed.bot_mentioned is True
    assert parsed.text == "200k An và Bình"
    assert parsed.sender_id == "29:sender" and parsed.sender_aad == "aad-x"
    assert len(parsed.image_attachments) == 1
    assert parsed.has_file_attachment is True


def test_parse_activity_no_bot_mention():
    activity = {"id": "a", "type": "message", "text": "hello", "from": {"id": "29:s"}, "entities": []}
    parsed = parse_activity(activity, bot_app_id="x", bot_handle="chiatienan")
    assert parsed.bot_mentioned is False
    assert parsed.text == "hello"
