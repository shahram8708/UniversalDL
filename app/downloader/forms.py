from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, URL, ValidationError


class DownloadForm(FlaskForm):
    url = StringField(
        "URL",
        validators=[DataRequired(), URL(require_tld=True), Length(max=2048)],
    )
    quality = SelectField(
        "Quality",
        choices=[
            ("best", "Best Available"),
            ("4K", "4K"),
            ("2K", "2K"),
            ("1080p", "1080p HD"),
            ("720p", "720p"),
            ("480p", "480p"),
            ("360p", "360p"),
            ("audio_only", "Audio Only"),
        ],
        default="best",
    )
    format = SelectField(
        "Format",
        choices=[
            ("mp4", "MP4 Video"),
            ("mkv", "MKV Video"),
            ("webm", "WebM Video"),
            ("mp3", "MP3 Audio"),
            ("flac", "FLAC Audio"),
            ("m4a", "M4A Audio"),
            ("jpg", "JPG Image"),
            ("jpeg", "JPEG Image"),
            ("png", "PNG Image"),
        ],
        default="mp4",
    )
    subtitle_language = StringField("Subtitle language", validators=[Optional(), Length(max=10)])
    subtitle_embed = BooleanField("Embed subtitles", default=False)
    embed_metadata = BooleanField("Embed metadata", default=True)
    submit = SubmitField("Analyze URL")

    def validate_url(self, field):
        value = field.data or ""
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValidationError("URL must start with http:// or https://")
        lowered = value.lower()
        blocked = ["https://google.com", "https://www.google.com", "http://google.com", "http://www.google.com"]
        if lowered in blocked or lowered.rstrip("/") in {"https://google.com", "https://www.google.com"}:
            raise ValidationError("Please enter a direct media URL, not a homepage.")


class BatchDownloadForm(FlaskForm):
    urls = TextAreaField("Paste URLs (one per line)", validators=[DataRequired(), Length(max=50000)])
    default_quality = SelectField(
        "Default Quality",
        choices=[
            ("best", "Best Available"),
            ("4K", "4K"),
            ("2K", "2K"),
            ("1080p", "1080p HD"),
            ("720p", "720p"),
            ("480p", "480p"),
            ("360p", "360p"),
            ("audio_only", "Audio Only"),
        ],
        default="best",
    )
    default_format = SelectField(
        "Default Format",
        choices=[
            ("mp4", "MP4 Video"),
            ("mkv", "MKV Video"),
            ("webm", "WebM Video"),
            ("mp3", "MP3 Audio"),
            ("flac", "FLAC Audio"),
            ("m4a", "M4A Audio"),
            ("jpg", "JPG Image"),
            ("jpeg", "JPEG Image"),
            ("png", "PNG Image"),
        ],
        default="mp4",
    )
    notify_email = BooleanField("Email me when batch completes", default=False)
    submit = SubmitField("Start Batch Download")
