import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "receiver_comm.settings")
application = get_wsgi_application()
