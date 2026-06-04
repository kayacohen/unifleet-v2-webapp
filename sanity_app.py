"""Minimal Flask app for the F1.1 Railway platform sanity check.

This app is the deployable entry point of the Railway `web` service for
the duration of F1.1. It exists only to prove the platform works:
- gunicorn can serve it
- HEAD and GET both return 200 (Railway's proxy may send HEAD)
- it does not import from main.py or any heavy native dependency

It is replaced by main:app in F1.6.
"""
from flask import Flask

app = Flask(__name__)


@app.route("/", methods=["GET", "HEAD"])
def root():
    return "ok", 200
