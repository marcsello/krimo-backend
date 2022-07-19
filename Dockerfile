FROM python:3.8

ADD requirements.txt /

RUN pip3 install -r requirements.txt

ADD krimo_backend/app.py /

EXPOSE 8000
CMD gunicorn -b 0.0.0.0:8000 app:app
