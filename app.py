# app.py — VidText for youtube-transcript-api v1.2.x
import re
from dataclasses import dataclass
from typing import Optional, List
from collections import Counter

import streamlit as st
from pytube import YouTube
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound, CouldNotRetrieveTranscript
from youtube_transcript_api.formatters import JSONFormatter
from youtube_transcript_api.proxies import WebshareProxyConfig, GenericProxyConfig

# ===============================
# Streamlit page config
# ===============================
st.set_page_config(page_title="🎬 VidText – YouTube Transcript Reporter", page_icon="📝", layout="centered")
st.title("🎬 VidText – YouTube Transcript Reporter")
st.write("Paste a YouTube link to fetch the transcript and generate a quick report of what it says.")

# ===============================
# Helpers
# ===============================
def get_video_id(youtube_url: str) -> str:
    youtube_url = youtube_url.strip()
    patterns = [r"(?:v=)([A-Za-z0-9_-]{11})", r"youtu\.be/([A-Za-z0-9_-]{11})", r"youtube\.com/embed/([A-Za-z0-9_-]{11})"]
    for p in patterns:
        m = re.search(p, youtube_url)
        if m:
            return m.group(1)
    raise ValueError("Invalid YouTube URL. Could not extract a video id.")

@st.cache_data(show_spinner=False)
def fetch_video_meta(url: str):
    yt = YouTube(url)
    return {
        "title": yt.title,
        "author": yt.author,
        "length_s": yt.length,
        "thumbnail_url": yt.thumbnail_url,
        "views": yt.views,
        "publish_date": yt.publish_date.isoformat() if yt.publish_date else None,
    }

def seconds_to_hms(sec: int) -> str:
    h = sec // 3600; m = (sec % 3600) // 60; s = sec % 60
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"

STOPWORDS = set("""
a an and the this that those these to of for from on in with as at by it its be is are was were am been being do does did doing have has had having i you he she they we him her them us our your their my mine yours his hers theirs myself yourself themselves itself themselves ourselves
about above after again against all also among around because before below between both but can cannot could did do does doing down during each few further here how if into more most no nor not only other out over own same should so some such than then there through too under until up very what when where which who whom why will would
""".split())

def clean_tokens(text: str):
    text = re.sub(r"[^A-Za-z0-9\s']", " ", text)
    words = [w.lower().strip("'") for w in text.split()]
    return [w for w in words if w and w not in STOPWORDS and not w.isdigit()]

def keyword_top_n(text: str, n=20):
    return Counter(clean_tokens(text)).most_common(n)

def split_sentences(text: str):
    parts = re.split(r'(?<=[\.\!\?])\s+', text)
    return [p.strip() for p in parts if p.strip()]

def score_sentences(sentences: List[str]):
    all_words = clean_tokens(" ".join(sentences))
    if not all_words:
        return []
    freqs = Counter(all_words)
    scored = []
    for s in sentences:
        toks = clean_tokens(s)
        if not toks:
            continue
        score = sum(freqs[t] for t in toks) / (len(toks) ** 0.8)
        scored.append((s, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored

def extract_summary_bullets(text: str, k=5, min_len=40, max_len=220):
    sents = split_sentences(text)
    ranked = score_sentences(sents)
    bullets, used = [], set()
    for s, _ in ranked:
        if len(s) < min_len or len(s) > max_len:
            continue
        sig = s[:40].lower()
        if sig in used:
            continue
        bullets.append("• " + s)
        used.add(sig)
        if len(bullets) >= k:
            break
    if not bullets and sents:
        bullets = ["• " + s for s in sents[:k]]
    return bullets

def fetched_to_plain_text(fetched):
    """v1.2.x returns FetchedTranscript with .to_raw_data()"""
    data = fetched.to_raw_data()
    return "\n".join(seg["text"] for seg in data if seg.get("text"))

def fetched_to_json_bytes(fetched):
    return JSONFormatter().format_transcript(fetched).encode("utf-8")

# ===============================
# Robust transcript discovery & fetch (v1.2.x)
# ===============================
@dataclass
class TranscriptChoice:
    language: str
    language_code: str
    is_generated: bool
    is_translatable: bool

@st.cache_data(show_spinner=False)
def list_available(ytt_api: YouTubeTranscriptApi, video_id: str):
    tlist = ytt_api.list(video_id)  # TranscriptList
    items = []
    for tr in tlist:
        items.append(TranscriptChoice(
            language=tr.language,
            language_code=tr.language_code,
            is_generated=tr.is_generated,
            is_translatable=tr.is_translatable,
        ))
    return tlist, items

def pick_and_fetch(ytt_api: YouTubeTranscriptApi, video_id: str, language_priority: Optional[List[str]]):
    # 1) try exact language preference (manual > auto is default behavior)
    if language_priority:
        try:
            tr = ytt_api.list(video_id).find_transcript(language_priority)
            return tr.fetch(), {
                "source": "direct",
                "picked": tr.language_code,
                "generated": tr.is_generated,
                "language": tr.language,
                "language_code": tr.language_code,
            }
        except Exception:
            pass

    # 2) translate any available to English
    tlist = ytt_api.list(video_id)
    for tr in tlist:
        try:
            if tr.is_translatable:
                translated = tr.translate("en")
                f = translated.fetch()
                return f, {
                    "source": "translated_to_en",
                    "picked": tr.language_code,
                    "generated": tr.is_generated,
                    "language": tr.language,
                    "language_code": tr.language_code,
                }
        except Exception:
            continue

    # 3) fallback to first available
    first = next(iter(tlist))
    f = first.fetch()
    return f, {
        "source": "fallback_first_available",
        "picked": first.language_code,
        "generated": first.is_generated,
        "language": first.language,
        "language_code": first.language_code,
    }

# ===============================
# UI – Inputs
# ===============================
url = st.text_input("Paste a YouTube link", placeholder="https://www.youtube.com/watch?v=XXXXXXXXXXX")

colA, colB = st.columns([2,1])
with colA:
    lang_pref = st.multiselect(
        "Preferred transcript languages (try in order)",
        ["en", "en-US", "en-GB", "es", "fr", "de", "auto"],
        default=["en", "en-US", "en-GB"]
    )
with colB:
    less_strict = st.checkbox("Be less strict", value=True, help="Ignore language preferences if needed.")

with st.expander("Advanced (optional) – Proxy (recommended if YouTube blocks your IP)"):
    proxy_mode = st.selectbox("Proxy mode", ["None", "Webshare (Residential)", "Generic HTTP/HTTPS"], index=0)
    webshare_user = webshare_pass = None
    filter_locs = []
    http_url = https_url = None

    if proxy_mode == "Webshare (Residential)":
        webshare_user = st.text_input("Webshare proxy username")
        webshare_pass = st.text_input("Webshare proxy password", type="password")
        locs = st.text_input("Filter IP locations (comma-separated country codes, optional)", value="")
        filter_locs = [x.strip().lower() for x in locs.split(",") if x.strip()]
    elif proxy_mode == "Generic HTTP/HTTPS":
        http_url = st.text_input("HTTP proxy URL (e.g., http://user:pass@host:port)")
        https_url = st.text_input("HTTPS proxy URL (e.g., https://user:pass@host:port)")

run = st.button("Fetch transcript & generate report", type="primary")

# ===============================
# Main action
# ===============================
if run:
    # Build API client (v1.2.x is instance-based)
    proxy_config = None
    if proxy_mode == "Webshare (Residential)" and webshare_user and webshare_pass:
        proxy_config = WebshareProxyConfig(proxy_username=webshare_user, proxy_password=webshare_pass,
                                           filter_ip_locations=filter_locs if filter_locs else None)
    elif proxy_mode == "Generic HTTP/HTTPS" and (http_url or https_url):
        proxy_config = GenericProxyConfig(http_url=http_url or None, https_url=https_url or None)

    ytt_api = YouTubeTranscriptApi(proxy_config=proxy_config) if proxy_config else YouTubeTranscriptApi()

    # --- Get video id
    try:
        vid = get_video_id(url)
    except ValueError as e:
        st.error(str(e)); st.stop()

    # --- Metadata (non-fatal)
    meta = None
    try:
        meta = fetch_video_meta(url)
    except Exception:
        pass

    # --- Fetch transcripts
    with st.spinner("Fetching transcript…"):
        # 1) Show availability if possible
        available_meta = None
        try:
            _, items = list_available(ytt_api, vid)
            available_meta = items
        except (TranscriptsDisabled, NoTranscriptFound):
            available_meta = []
        except Exception:
            available_meta = None  # we'll still try picker

        st.markdown("**Available transcripts**")
        if available_meta is None:
            st.info("Could not list transcripts (will still attempt to fetch).")
        elif not available_meta:
            st.info("No transcripts listed by YouTube for this video.")
        else:
            for ch in available_meta:
                st.write(
                    f"- {ch.language} ({ch.language_code}) "
                    f"{'• auto' if ch.is_generated else '• manual'} "
                    f"{'• translatable' if ch.is_translatable else ''}"
                )

        # 2) Pick + fetch
        try:
            languages = None if less_strict else [l for l in lang_pref if l != "auto"]
            fetched, pick_info = pick_and_fetch(ytt_api, vid, language_priority=languages)
        except TranscriptsDisabled:
            st.error("This video has transcripts disabled by the uploader."); st.stop()
        except NoTranscriptFound:
            st.error("No transcript found for this video (including auto-captions)."); st.stop()
        except CouldNotRetrieveTranscript:
            st.error("YouTube blocked the transcript request (rate limit, age/region lock). Consider enabling a proxy above."); st.stop()
        except Exception as e:
            st.error(f"Couldn't fetch transcript: {e}")
            with st.expander("Troubleshooting"):
                st.markdown("""
- Some videos simply have **no captions**.
- Enable **Proxy** above if YouTube blocks your IP (cloud/VPN IPs often get blocked).
- Toggle **Be less strict** to allow any available language or auto-captions.
                """)
            st.stop()

    # --- Build report
    text = fetched_to_plain_text(fetched)

    # Header + meta
    if meta:
        st.markdown(f"### {meta.get('title','Video')}")
        c1, c2 = st.columns([1,2])
        with c1:
            if meta.get("thumbnail_url"):
                st.image(meta["thumbnail_url"], use_column_width=True)
        with c2:
            st.markdown(f"**Channel:** {meta.get('author','—')}")
            if meta.get("length_s") is not None:
                st.markdown(f"**Length:** {seconds_to_hms(meta['length_s'])}")
            if meta.get("views") is not None:
                st.markdown(f"**Views:** {meta['views']:,}")
            if meta.get("publish_date"):
                st.markdown(f"**Published:** {meta['publish_date'][:10]}")
    else:
        st.markdown("### Video")

    # Picking info
    st.caption(
        f"Transcript source: {pick_info['source']} | "
        f"language: {pick_info['language']} ({pick_info['language_code']}) | "
        f"{'auto' if pick_info['generated'] else 'manual'}"
    )

    st.divider()

    # Downloads
    st.subheader("Downloads")
    st.download_button("⬇️ Transcript (.txt)", data=text.encode("utf-8"), file_name="youtube_transcript.txt", mime="text/plain")
    st.download_button("⬇️ Transcript (.json)", data=fetched_to_json_bytes(fetched), file_name="youtube_transcript.json", mime="application/json")

    st.divider()

    # Quick Report
    st.subheader("Quick Report")
    words = clean_tokens(text)
    st.markdown(
        f"""
- **Transcript length:** {len(text.split()):,} words  
- **Unique words (filtered):** {len(set(words)):,}  
- **Segments:** {len(fetched):,}
        """
    )

    st.markdown("**Top keywords**")
    topk = keyword_top_n(text, n=20)
    st.write(", ".join([f"{w} ({c})" for w, c in topk[:15]]) if topk else "—")

    st.markdown("**Auto-summary (extractive)**")
    bullets = extract_summary_bullets(text, k=5)
    st.markdown("\n".join(bullets) if bullets else "—")

    with st.expander("Full transcript", expanded=False):
        st.text(text)

# Footer
st.caption("Tip: If a transcript isn’t available, try loosening language preferences or enabling a proxy (Webshare/Generic). Cookie auth is not currently supported by the library.")
