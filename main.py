import os
import re
import json
import math
from urllib.parse import urlencode, quote_plus
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

def build_ytmusic_query(keywords):
    if isinstance(keywords, str):
        raw = keywords
    else:
        raw = " ".join(keywords)
    raw = raw.strip().replace(",", " ")
    raw = re.sub(r"\s+", " ", raw)
    return raw

def ytmusic_search_url(query):
    base = "https://music.youtube.com/search?q="
    return base + quote_plus(query)

def _http_get_json(base_url, params):
    url = f"{base_url}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(f"HTTPError {e.code}: {e.read().decode('utf-8', errors='ignore')}") from e
    except URLError as e:
        raise RuntimeError(f"URLError: {e.reason}") from e

def _clean_title(s):
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"\((?:official|mv|m/v|lyric|audio|teaser|performance|ver\.?|version|shorts|full album|visualizer)[^)]*\)", "", s, flags=re.I)
    s = re.sub(r"(?i)\b(official\s*(video)?|mv|m/v|lyric\s*video|audio|teaser|visualizer|shorts)\b", "", s)
    s = re.sub(r"\s+\|\s*.*$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -|")
    return s.strip()

def extract_title_artist(video_title, channel_title):
    t = _clean_title(video_title or "")
    c = (channel_title or "").strip()
    patterns = [
        r"^\s*(?P<artist>.+?)\s*-\s*(?P<title>.+?)\s*$",
        r"^\s*(?P<title>.+?)\s*-\s*(?P<artist>.+?)\s*$",
        r"^\s*(?P<artist>.+?)\s*—\s*(?P<title>.+?)\s*$",
    ]
    for p in patterns:
        m = re.match(p, t)
        if m:
            artist = _clean_title(m.group("artist"))
            title = _clean_title(m.group("title"))
            if artist and title:
                return title, artist
    if c and c.lower().endswith(" - topic"):
        c = c[:-8].strip()
    if t and c:
        return t, c
    return (t or video_title or "").strip(), (c or channel_title or "").strip()

def youtube_search_music(query, max_results=10, region="KR", order="viewCount"):
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise RuntimeError("환경변수 YOUTUBE_API_KEY가 설정되어 있지 않습니다.")

    search_params = {
        "key": api_key,
        "part": "snippet",
        "q": query,
        "type": "video",
        "videoCategoryId": "10",  # Music
        "maxResults": max_results,
        "order": order,
        "regionCode": region,
        "safeSearch": "none",
    }
    search_data = _http_get_json("https://www.googleapis.com/youtube/v3/search", search_params)

    video_ids = [item["id"]["videoId"] for item in search_data.get("items", []) if item.get("id", {}).get("videoId")]
    if not video_ids:
        return []

    videos_params = {
        "key": api_key,
        "part": "snippet,contentDetails,statistics",
        "id": ",".join(video_ids),
        "maxResults": max_results,
    }
    videos_data = _http_get_json("https://www.googleapis.com/youtube/v3/videos", videos_params)

    results = []
    for item in videos_data.get("items", []):
        vid = item.get("id")
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        title_raw = snippet.get("title") or ""
        channel = snippet.get("channelTitle") or ""
        song_title, artist = extract_title_artist(title_raw, channel)
        results.append({
            "song_title": song_title,
            "artist": artist,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": title_raw,
            "channel": channel,
            "views": int(stats.get("viewCount", 0)) if stats.get("viewCount") else 0,
        })

    results.sort(key=lambda x: x["views"], reverse=True)
    return results[:max_results]

def build_user_prefs(keywords):
    kws = []
    if isinstance(keywords, str):
        kws = [k.strip() for k in keywords.split(",") if k.strip()]
    else:
        kws = [str(k).strip() for k in keywords if str(k).strip()]
    kws_lower = [k.lower() for k in kws]
    return {
        "keywords": kws,
        "keywords_lower": kws_lower,
        "penalty_terms": ["live", "cover", "remix"],
    }

def score_item(item, prefs):
    title = (item.get("song_title") or item.get("title") or "").lower()
    artist = (item.get("artist") or item.get("channel") or "").lower()
    text = f"{title} {artist}"

    # 키워드 매칭 점수
    kw_score = 0.0
    for kw in prefs["keywords_lower"]:
        if not kw:
            continue
        # 전체 단어 포함 가중
        if kw in text:
            kw_score += 2.0
        # 제목/아티스트 각각 포함 시 추가 가중
        if kw in title:
            kw_score += 1.0
        if kw in artist:
            kw_score += 1.0

    # 인기(조회수) 로그 스케일
    views = item.get("views", 0) or 0
    pop_score = math.log10(views + 1)  # 0 ~ 대략 7

    # 일반 감점 (선호 키워드에 live/cover/remix가 포함된 경우는 감점 제외)
    penalty = 0.0
    kw_has_penalty_terms = any(t in prefs["keywords_lower"] for t in prefs["penalty_terms"])
    if not kw_has_penalty_terms:
        for t in prefs["penalty_terms"]:
            if re.search(rf"\b{re.escape(t)}\b", text):
                penalty += 1.0

    # 가중합
    score = kw_score * 1.5 + pop_score * 0.7 - penalty * 0.8
    return score

def rank_recommendations(items, prefs):
    ranked = []
    for it in items:
        s = score_item(it, prefs)
        it2 = dict(it)
        it2["score"] = round(s, 3)
        ranked.append(it2)
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked

user_input = input("좋아하는 음악 장르나 키워드를 입력하세요 (여러 개는 쉼표로 구분): ").strip()

if not user_input:
    print("입력이 비어 있습니다. 프로그램을 종료합니다.")
else:
    keywords = [k.strip() for k in user_input.split(",") if k.strip()]
    query = build_ytmusic_query(keywords)
    print(f"유튜브 뮤직 검색어: {query}")
    print(f"검색 URL: {ytmusic_search_url(query)}")

    try:
        items = youtube_search_music(query, max_results=10, region="KR", order="viewCount")
        if not items:
            print("검색 결과가 없습니다.")
        else:
            print("\n인기 음악 영상 결과:")
            for i, it in enumerate(items, 1):
                views_fmt = f"{it['views']:,}" if isinstance(it['views'], int) else it['views']
                print(f"{i}. {it['song_title']} - {it['artist']} | 조회수 {views_fmt} | {it['url']}")

            prefs = build_user_prefs(keywords)
            ranked = rank_recommendations(items, prefs)

            print("\n추천 순위(선호도 반영):")
            for i, it in enumerate(ranked, 1):
                views_fmt = f"{it['views']:,}" if isinstance(it['views'], int) else it['views']
                print(f"{i}. {it['song_title']} - {it['artist']} | 점수 {it['score']} | 조회수 {views_fmt} | {it['url']}")
    except RuntimeError as e:
        print(f"API 호출 중 오류: {e}")


from ytmusicapi import YTMusic

def _get_ytm_client():
    headers_path = os.getenv("YTMUSIC_HEADERS", "headers_auth.json")
    return YTMusic(headers_path)

def _extract_video_ids(urls):
    ids = []
    for u in urls:
        m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", u)
        if m:
            ids.append(m.group(1))
    return ids

def create_ytmusic_playlist(title, description, video_urls, privacy="PRIVATE"):
    ytm = _get_ytm_client()
    video_ids = _extract_video_ids(video_urls)
    pl_id = ytm.create_playlist(title=title, description=description, privacy_status=privacy)
    if video_ids:
        ytm.add_playlist_items(pl_id, video_ids)
    return pl_id