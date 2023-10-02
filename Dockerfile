FROM python:slim

WORKDIR /usr/src/app

COPY requirements.txt . 

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY restarter ./restarter

CMD [ "python", "-u", "./main.py" ]
