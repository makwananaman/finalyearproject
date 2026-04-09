from django.urls import path
from . import views

urlpatterns = [
    path('', views.email_dashboard, name='email_dashboard'),
    path("gmail/connect/", views.connect_gmail, name="connect_gmail"),
    path("gmail/callback/", views.gmail_callback, name="gmail_callback"),
    path("gmail/fetch/", views.fetch_emails_view, name="fetch_emails"),
]
