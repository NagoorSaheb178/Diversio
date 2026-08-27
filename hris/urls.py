from django.urls import path
from hris import views

urlpatterns = [
    path("", views.upload_view, name="upload"),
    path("preview/", views.preview_view, name="preview"),
]
