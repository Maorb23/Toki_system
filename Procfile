web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn receiver_comm.wsgi:application --log-file -
