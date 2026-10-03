"""推文正文中的 t.co 短链应还原为 entities 里的原始链接。"""

from parsehub.provider_api.twitter import Twitter

SHORT = "https://t.co/79NDWg9zsJ"
EXPANDED = "https://codeforces.com/"


def url_entity(short=SHORT, expanded=EXPANDED):
    return {"url": short, "expanded_url": expanded, "display_url": "codeforces.com"}


def make_payload(full_text, urls=None, media=None, note_text=None, note_urls=None):
    legacy = {"full_text": full_text, "entities": {"urls": urls or [], "media": media or []}}
    result = {"rest_id": "1", "legacy": legacy}
    if note_text is not None:
        result["note_tweet"] = {
            "note_tweet_results": {"result": {"text": note_text, "entity_set": {"urls": note_urls or []}}}
        }
    return {"data": {"tweetResult": {"result": result}}}


def test_plain_tweet_restores_short_url():
    payload = make_payload(f"Telegram has become the sponsor of {SHORT} — codeforces", urls=[url_entity()])
    tweet = Twitter().parse(payload)
    assert tweet.full_text == "Telegram has become the sponsor of https://codeforces.com/ — codeforces"
    assert "t.co" not in tweet.full_text


def test_note_tweet_restores_short_url_from_entity_set():
    payload = make_payload("legacy fallback", note_text=f"长文正文 {SHORT}", note_urls=[url_entity()])
    tweet = Twitter().parse(payload)
    assert tweet.full_text == "长文正文 https://codeforces.com/"


def test_note_tweet_without_text_falls_back_to_legacy_entities():
    payload = make_payload(f"legacy {SHORT}", urls=[url_entity()], note_text="")
    tweet = Twitter().parse(payload)
    assert tweet.full_text == "legacy https://codeforces.com/"


def test_media_tweet_keeps_text_link_and_drops_trailing_media_short_url():
    media = [
        {
            "type": "photo",
            "media_url_https": "https://pbs.twimg.com/media/abc.jpg",
            "original_info": {"width": 1, "height": 1},
        }
    ]
    payload = make_payload(f"源码见 {SHORT} https://t.co/MediaTail", urls=[url_entity()], media=media)
    tweet = Twitter().parse(payload)
    assert tweet.full_text == "源码见 https://codeforces.com/"
    assert tweet.media and len(tweet.media) == 1


def test_missing_entities_is_a_noop():
    payload = make_payload("没有链接的正文")
    tweet = Twitter().parse(payload)
    assert tweet.full_text == "没有链接的正文"


def test_incomplete_entity_is_skipped():
    payload = make_payload(f"正文 {SHORT}", urls=[{"url": SHORT}])
    tweet = Twitter().parse(payload)
    assert SHORT in tweet.full_text
