import logging
from urllib.parse import urlparse

from app.extractors import (
    BaseExtractor,
    ExtractorError,
    BilibiliExtractor,
    DailymotionExtractor,
    FacebookExtractor,
    GenericExtractor,
    InstagramExtractor,
    ImgurExtractor,
    LinkedInExtractor,
    NetflixExtractor,
    PinterestExtractor,
    PodcastRSSExtractor,
    RedditExtractor,
    SoundCloudExtractor,
    SpotifyExtractor,
    TikTokExtractor,
    TwitchExtractor,
    TwitterExtractor,
    VimeoExtractor,
    YoutubeExtractor,
    CourseraExtractor,
    BehanceExtractor,
)


logger = logging.getLogger(__name__)

EXTRACTOR_REGISTRY = {
    "youtube": YoutubeExtractor,
    "tiktok": TikTokExtractor,
    "instagram": InstagramExtractor,
    "twitter": TwitterExtractor,
    "reddit": RedditExtractor,
    "twitch": TwitchExtractor,
    "vimeo": VimeoExtractor,
    "soundcloud": SoundCloudExtractor,
    "bilibili": BilibiliExtractor,
    "facebook": FacebookExtractor,
    "dailymotion": DailymotionExtractor,
    "spotify": SpotifyExtractor,
    "linkedin": LinkedInExtractor,
    "pinterest": PinterestExtractor,
    "podcast_rss": PodcastRSSExtractor,
    "netflix": NetflixExtractor,
    "coursera": CourseraExtractor,
    "behance": BehanceExtractor,
    "imgur": ImgurExtractor,
    "generic": GenericExtractor,
}


def _mask_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return url
    path = parsed.path or ""
    short_path = path[:24]
    if len(path) > 24:
        short_path = f"{short_path}..."
    return f"{parsed.scheme}://{parsed.netloc}{short_path}"


def get_extractor(platform_id: str, proxy_pool=None) -> BaseExtractor:
    extractor_cls = EXTRACTOR_REGISTRY.get(platform_id, GenericExtractor)
    return extractor_cls(proxy_pool=proxy_pool)


def dispatch_and_extract(platform_id: str, url: str, proxy_pool=None) -> dict:
    extractor = get_extractor(platform_id, proxy_pool=proxy_pool)
    try:
        media_info = extractor.extract(url)
        if not isinstance(media_info, dict):
            logger.warning(
                "Extractor returned non-dict metadata for %s at %s. Retrying once.",
                platform_id,
                _mask_url(url),
            )
            extractor = get_extractor(platform_id, proxy_pool=proxy_pool)
            media_info = extractor.extract(url)

        if not isinstance(media_info, dict):
            raise ExtractorError(
                "Extractor returned invalid media metadata",
                platform=platform_id,
                url=url,
                data={"type": type(media_info).__name__},
            )
        if "title" not in media_info or "qualities" not in media_info:
            raise ExtractorError(
                "Extractor returned incomplete media metadata",
                platform=platform_id,
                url=url,
                data={"keys": sorted(media_info.keys())},
            )
        logger.info("Extraction success for %s at %s", platform_id, _mask_url(url))
        return media_info
    except ExtractorError:
        logger.warning("Extraction validation failed for %s at %s", platform_id, _mask_url(url), exc_info=True)
        # Try a generic extractor fallback to still locate downloadable media
        try:
            generic = get_extractor("generic", proxy_pool=proxy_pool)
            media_info = generic.extract(url)
            if isinstance(media_info, dict) and "qualities" in media_info:
                logger.info("Generic fallback success for %s at %s", platform_id, _mask_url(url))
                return media_info
        except Exception:
            logger.debug("Generic fallback also failed for %s at %s", platform_id, _mask_url(url), exc_info=True)
        raise
    except Exception as exc:
        logger.error("Extraction failed for %s url %s. Error: %s", platform_id, url, exc, exc_info=True)
        raise
