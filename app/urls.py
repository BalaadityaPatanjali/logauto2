from django.urls import path
from . import views
from .views import summarize_logs

urlpatterns = [
    path('', views.index, name='index'),
    path('summarize/', summarize_logs, name='summarize_logs'),
    path("analyze/", views.analyze_logs, name="analyze_logs"),
]
