from django.urls import path
from . import views

app_name = 'park_system'

urlpatterns = [
    path('', views.index, name='index'),
    path('vehicles/', views.vehicles, name='vehicles'),
    path('add_vehicle/', views.add_vehicle, name='add_vehicle'),

    # Sessions
    path("vehicles/<int:vehicle_id>/start-session/", views.start_session, name="start_session"),
    path("vehicles/<int:vehicle_id>/entry/", views.entry, name="entry"),
    path("vehicles/<int:vehicle_id>/exit_park/", views.exit_park, name="exit_park"),
    path('attendant/start-session/<int:vehicle_id>/', views.attendant_start_session, name='attendant_start_session'),

    # Tickets
    path("ticket/<uuid:code>/", views.ticket_detail, name="ticket_detail"),
    path('end_session/<uuid:code>/', views.end_session, name='end_session'),
    path('tickets/', views.tickets, name='tickets'),
    path('ticket/<uuid:code>/reopen/', views.reopen_ticket, name='reopen_ticket'),

    # Payment — customer
    path("tickets/<uuid:code>/pay/", views.mark_paid, name="mark_paid"),
    path('payment/initiate/<uuid:code>/', views.initiate_payment, name='initiate_payment'),
    path('payment/callback/<str:reference>/', views.payment_callback, name='payment_callback'),

    # Payment — attendant
    path("attendant/tickets/<uuid:code>/pay/", views.attendant_mark_paid, name="attendant_mark_paid"),
    path('payment/attendant/<uuid:code>/', views.attendant_initiate_payment, name='attendant_initiate_payment'),

    # Webhook — no auth, Paystack posts here
    path('payment/webhook/', views.paystack_webhook, name='paystack_webhook'),

    # Vehicles
    path('delete_vehicle/<int:vehicle_id>/', views.delete_vehicle, name='delete_vehicle'),
    path('attendant/add-vehicle/', views.attendant_add_vehicle, name='attendant_add_vehicle'),

    # Users
    path('register-attendant/', views.register_attendant, name='register_attendant'),
    path('register-customer/', views.register_customer, name='register_customer'),

    # Admin
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('audit/', views.audit_logs, name='audit_logs'),
]