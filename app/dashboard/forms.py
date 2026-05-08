from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    EmailField,
    HiddenField,
    PasswordField,
    RadioField,
    SelectField,
    StringField,
    SubmitField,
    URLField,
)
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, URL, ValidationError


QUALITY_CHOICES = [
    ("best", "Best Available"),
    ("1080p", "1080p HD"),
    ("720p", "720p"),
    ("480p", "480p"),
    ("audio_only", "Audio Only"),
]

FORMAT_CHOICES = [
    ("mp4", "MP4 Video"),
    ("mkv", "MKV Video"),
    ("mp3", "MP3 Audio"),
    ("m4a", "M4A Audio"),
]


class SubscriptionForm(FlaskForm):
    channel_url = URLField(
        "Channel or Feed URL",
        validators=[DataRequired(), URL(require_tld=True), Length(max=2048)],
        description="Paste a YouTube channel, podcast RSS, or any supported URL",
    )
    frequency = SelectField(
        "Frequency",
        choices=[
            ("daily", "Daily (check once per day)"),
            ("weekly", "Weekly (check once per week)"),
            ("hourly", "Hourly - Pro only"),
        ],
        default="daily",
    )
    quality = SelectField("Quality", choices=QUALITY_CHOICES, default="best")
    format = SelectField("Format", choices=FORMAT_CHOICES, default="mp4")
    notification_email = BooleanField(
        "Email me when new content is downloaded",
        default=True,
    )
    submit = SubmitField("Add Subscription")

    def validate_frequency(self, field):
        if field.data == "hourly" and not current_user.is_pro():
            raise ValidationError("Hourly polling is a Pro feature. Please upgrade your plan.")


class AccountSettingsForm(FlaskForm):
    display_name = StringField("Display Name", validators=[Optional(), Length(max=100)])
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    submit = SubmitField("Save Changes")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current Password", validators=[DataRequired()])
    new_password = PasswordField("New Password", validators=[DataRequired(), Length(min=10)])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match")],
    )
    submit = SubmitField("Change Password")


class PreferencesForm(FlaskForm):
    default_quality = SelectField("Default Quality", choices=QUALITY_CHOICES, default="best")
    default_format = SelectField("Default Format", choices=FORMAT_CHOICES, default="mp4")
    email_notifications = BooleanField("Email notifications")
    anonymous_mode = BooleanField("Anonymous mode (URLs not stored)")
    submit = SubmitField("Save Preferences")


class HistoryFilterForm(FlaskForm):
    search = StringField("Search by title or URL", validators=[Optional(), Length(max=200)])
    platform = SelectField("Platform", choices=[("", "All Platforms")])
    date_from = DateField("From", validators=[Optional()], format="%Y-%m-%d")
    date_to = DateField("To", validators=[Optional()], format="%Y-%m-%d")
    content_type = SelectField(
        "Content Type",
        choices=[
            ("", "All Types"),
            ("video", "Video"),
            ("audio", "Audio"),
            ("image", "Image"),
            ("document", "Document"),
            ("post", "Post"),
        ],
    )
    status = SelectField(
        "Status",
        choices=[
            ("", "All Status"),
            ("complete", "Completed"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
    )
    submit = SubmitField("Apply Filters")

    def __init__(self, *args, **kwargs):
        platform_choices = kwargs.pop("platform_choices", None)
        super().__init__(*args, **kwargs)
        if platform_choices:
            self.platform.choices = platform_choices


class APIKeyForm(FlaskForm):
    key_name = StringField(
        "Key Name",
        validators=[DataRequired(), Length(min=1, max=100)],
        description="A label to identify this key (e.g., 'My Plex Server')",
    )
    submit = SubmitField("Generate API Key")


class DeleteAPIKeyForm(FlaskForm):
    key_prefix = HiddenField(validators=[DataRequired()])
    submit = SubmitField("Revoke Key")


class OnboardingStep1Form(FlaskForm):
    default_format = RadioField(
        "Preferred Output Format",
        choices=[
            ("mp4", "MP4 Video - Best for most devices"),
            ("mkv", "MKV Video - Best for archiving"),
            ("mp3", "MP3 Audio - Music and podcasts"),
            ("webm", "WebM - Best for web use"),
        ],
        default="mp4",
        validators=[DataRequired()],
    )
    default_quality = RadioField(
        "Default Download Quality",
        choices=[
            ("best", "Best Available - Highest quality always"),
            ("1080p", "Balanced (1080p) - Great quality, reasonable size"),
            ("720p", "Mobile (720p) - Good quality, smaller files"),
            ("480p", "Data Saver (480p) - For slow connections"),
        ],
        default="best",
        validators=[DataRequired()],
    )
    submit = SubmitField("Next Step >")


class OnboardingStep2Form(FlaskForm):
    email_notifications = BooleanField("Email me when downloads complete", default=True)
    anonymous_mode = BooleanField("Anonymous mode - don't store download URLs in my history", default=False)
    submit = SubmitField("Next Step >")


class OnboardingStep3Form(FlaskForm):
    submit = SubmitField("Finish Setup - Go to Dashboard")
