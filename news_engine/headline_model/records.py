"""Model-side article validation, independent of the news discovery package."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
from urllib.parse import urlsplit

from .engine import utc


@dataclass(frozen=True)
class Article:
    headline: str
    summary: str
    published_at: str
    url: str
    source: str

    @classmethod
    def parse(cls, value):
        headline, summary = value.get('headline'), value.get('summary', '')
        if not isinstance(headline, str) or not headline.strip() or len(headline) > 2000:
            raise ValueError('Provide an article headline up to 2,000 characters.')
        if not isinstance(summary, str) or len(summary) > 12000:
            raise ValueError('Provide a source summary up to 12,000 characters.')
        url = value.get('url', '')
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            raise ValueError('Provide an HTTP(S) source URL without credentials.')
        return cls(headline.strip(), summary, utc(value.get('published_at')).isoformat(), url, str(value.get('source', '')))

    @property
    def id(self):
        source_key = hashlib.sha256((self.url + '|' + self.published_at).encode()).hexdigest()
        return hashlib.sha256((source_key + '|' + self.headline + '|' + self.summary).encode()).hexdigest()

    def is_fresh(self, now, max_age=3600):
        if isinstance(max_age, bool) or not isinstance(max_age, int) or not 1 <= max_age <= 86400:
            raise ValueError('Headline age must be between one second and 24 hours.')
        return 0 <= (now - utc(self.published_at)).total_seconds() <= max_age
