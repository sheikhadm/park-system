from django.urls import path
from . import views
import uuid

app_name = 'park_system'

urlpatterns = [
    
    path('', views.index, name='index'),
    path('vehicles/', views.vehicles, name='vehicles'),
    # path('topics/<int:topic_id>/', views.topic, name='topic'),
    path('add_vehicle/', views.add_vehicle, name='add_vehicle'),
    path("vehicles/<int:vehicle_id>/start-session/", views.start_session, name="start_session"),
    path("ticket/<uuid:code>/", views.ticket_detail, name="ticket_detail"),
    path('end_session/<uuid:code>/', views.end_session, name='end_session'),
    path('tickets', views.tickets, name='tickets'),
    # path('delete_entry/<int:entry_id>/', views.delete_entry, name='delete_entry'),
    path('delete_vehicle/<int:vehicle_id>/', views.delete_vehicle, name='delete_vehicle'),

]