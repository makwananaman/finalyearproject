from django.urls import path

from . import views

urlpatterns = [
    path("gmail/connect/", views.connect_gmail, name="connect_gmail"),
    path("gmail/callback/", views.gmail_callback, name="gmail_callback"),
]
