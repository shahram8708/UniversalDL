from app.extractors.base import BaseExtractor, ExtractorError


class NetflixExtractor(BaseExtractor):
    PLATFORM_ID = "netflix"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://www.netflix.com/watch/70143836"

    def extract(self, url: str) -> dict:
        if "netflix.com" not in (url or "").lower():
            raise ExtractorError("Unsupported Netflix URL", platform=self.PLATFORM_ID, url=url)

        error_message = (
            "Netflix content is protected by Widevine DRM (Level 1 hardware encryption). "
            "This is a legally enforced digital rights protection system that cannot be "
            "bypassed by any software tool — including UniversalDL. "
            "Netflix explicitly prohibits downloading for offline use except through their "
            "official mobile app (which stores encrypted files that cannot be played outside Netflix). "
            "To watch Netflix offline legally, use the official Netflix app on Android or iOS "
            "which allows downloading for offline viewing within their ecosystem. "
            "UniversalDL will never attempt to circumvent DRM protection systems."
        )

        error = ExtractorError(error_message, platform=self.PLATFORM_ID, url=url, data={"is_drm": True})
        raise error
