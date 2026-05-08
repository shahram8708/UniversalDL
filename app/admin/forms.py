from flask_wtf import FlaskForm
from wtforms import DateField, HiddenField, IntegerField, SelectField, StringField, SubmitField, URLField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, URL


class ExtractorToggleForm(FlaskForm):
    extractor_id = HiddenField(validators=[DataRequired()])
    submit = SubmitField("Toggle")


class ExtractorTestForm(FlaskForm):
    extractor_id = HiddenField(validators=[DataRequired()])
    test_url = URLField(
        "Custom test URL (optional - uses default if empty)",
        validators=[Optional(), URL(), Length(max=2048)],
    )
    submit = SubmitField("Run Test")


class AdminUserSearchForm(FlaskForm):
    search = StringField("Search by email or display name", validators=[Optional(), Length(max=200)])
    plan_filter = SelectField(
        "Plan",
        choices=[("", "All Plans"), ("free", "Free"), ("pro", "Pro"), ("enterprise", "Enterprise")],
    )
    status_filter = SelectField(
        "Status",
        choices=[
            ("", "All Status"),
            ("active", "Active"),
            ("suspended", "Suspended"),
            ("admin", "Admin"),
        ],
    )
    date_from = DateField("Joined After", validators=[Optional()], format="%Y-%m-%d")
    submit = SubmitField("Search")


class GlobalSettingsForm(FlaskForm):
    max_concurrent_free = IntegerField(
        "Max concurrent downloads (Free users)",
        validators=[DataRequired(), NumberRange(min=1, max=10)],
        default=2,
    )
    max_concurrent_pro = IntegerField(
        "Max concurrent downloads (Pro users)",
        validators=[DataRequired(), NumberRange(min=1, max=50)],
        default=10,
    )
    max_batch_urls_free = IntegerField(
        "Max batch URLs (Free users)",
        validators=[DataRequired(), NumberRange(min=1, max=20)],
        default=5,
    )
    history_retention_days_free = IntegerField(
        "History retention days (Free users)",
        validators=[DataRequired(), NumberRange(min=1, max=90)],
        default=7,
    )
    ffmpeg_path = StringField(
        "FFmpeg binary path",
        validators=[Optional(), Length(max=500)],
        description="Leave empty to use system PATH",
    )
    temp_download_dir = StringField(
        "Temp download directory",
        validators=[Optional(), Length(max=500)],
    )
    max_file_age_seconds = IntegerField(
        "Max file age (seconds)",
        validators=[DataRequired(), NumberRange(min=300, max=86400)],
        default=3600,
    )
    submit = SubmitField("Save Settings")
