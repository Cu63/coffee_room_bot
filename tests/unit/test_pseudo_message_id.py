from bot.application.score_service import _next_pseudo_message_id


def test_pseudo_message_ids_are_unique_and_negative():
    ids = [_next_pseudo_message_id() for _ in range(10_000)]
    assert len(set(ids)) == len(ids)
    assert all(i < 0 for i in ids)
