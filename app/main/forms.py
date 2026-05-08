from flask_wtf import FlaskForm
from wtforms import EmailField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional


class ContactForm(FlaskForm):
    name = StringField("Your Name", validators=[DataRequired(), Length(min=2, max=100)])
    email = EmailField(
        "Email Address",
        validators=[DataRequired(), Email(), Length(max=255)],
    )
    subject = SelectField(
        "Subject",
        choices=[
            ("general", "General Inquiry"),
            ("bug_report", "Bug Report"),
            ("feature_request", "Feature Request"),
            ("billing", "Billing & Payments"),
            ("api_support", "API Support"),
            ("dmca", "DMCA / Copyright"),
            ("other", "Other"),
        ],
    )
    message = TextAreaField(
        "Message",
        validators=[DataRequired(), Length(min=20, max=5000)],
    )
    submit = SubmitField("Send Message")


class BlogSearchForm(FlaskForm):
    query = StringField("Search articles...", validators=[Optional(), Length(max=200)])
    submit = SubmitField("Search")
