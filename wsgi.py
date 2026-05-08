from dotenv import load_dotenv


load_dotenv()

from app import create_app


application = create_app()
app = application

# use_reloader is not applicable under Gunicorn; use: gunicorn --workers 4 wsgi:application
