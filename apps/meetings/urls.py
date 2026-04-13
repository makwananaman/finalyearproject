from django.urls import path

from . import views

urlpatterns = [
    path("", views.meetings_view, name="meeting_list"),
    path("status/", views.meeting_status_view, name="meeting_status"),
]
