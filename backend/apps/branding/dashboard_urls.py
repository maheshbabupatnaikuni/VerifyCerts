from django.urls import path

from .views import (
    dashboard_admins,
    dashboard_blockchain,
    dashboard_home,
    dashboard_logs,
    dashboard_review,
    dashboard_search,
    dashboard_upload,
    student_portal,
)

urlpatterns = [
    path("", dashboard_home, name="dashboard-home"),
    path("dashboard/upload/", dashboard_upload, name="dashboard-upload"),
    path("dashboard/search/", dashboard_search, name="dashboard-search"),
    path("dashboard/review/", dashboard_review, name="dashboard-review"),
    path("dashboard/logs/", dashboard_logs, name="dashboard-logs"),
    path("dashboard/blockchain/", dashboard_blockchain, name="dashboard-blockchain"),
    path("dashboard/admins/", dashboard_admins, name="dashboard-admins"),
    path("student/", student_portal, name="student-portal"),
]
