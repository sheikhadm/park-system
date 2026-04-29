from django.shortcuts import render,redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .decorators import admin_required, attendant_required, customer_required, role_required
from django.db import transaction, IntegrityError
from django.http import Http404, JsonResponse
from django_q.tasks import async_task, schedule
from django.utils import timezone
from datetime import timedelta
from .models import Vehicle,Ticket,ParkingSlot,AuditLog,Payment
from .paystack import initialize_transaction, verify_transaction, generate_reference
from .forms import VehicleForm , AttendantVehicleForm, AttendantRegistrationForm, CustomerRegistrationForm
from django.http import HttpResponse
from django.contrib import messages
from .audit import log_action
import logging
import math
from django.db.models import Sum, Count
from django.urls import reverse
import hmac
import hashlib
logger = logging.getLogger(__name__)
# Create your views here.

def index(request):
    return render(request, 'park_system/index.html')

@login_required
@customer_required
def add_vehicle(request):
    if request.method == "POST":
        form = VehicleForm(request.POST)
        if form.is_valid():
            vehicle = form.save(commit = False)
            vehicle.owner = request.user
            vehicle.save()  
            log_action(
                action=AuditLog.Action.VEHICLE_ADDED,
                performed_by=request.user,
                vehicle=vehicle,
                metadata={
                    "number_plate": vehicle.number_plate,
                    "vehicle_type": vehicle.vehicle_type,
                    "vehicle_make": vehicle.vehicle_make,
                    "owner": request.user.username,
                }
            ) 
            return redirect("park_system:index")  
    else:
        form = VehicleForm()

    return render(request, "park_system/add_vehicle.html", {"form": form})

@login_required
def vehicles(request):
    if request.user.profile.is_admin or request.user.profile.is_attendant:
        vehicles = Vehicle.objects.all()
    else:
        vehicles = Vehicle.objects.filter(owner=request.user)
    context = { "vehicles": vehicles }
    return render(request, "park_system/vehicles.html", context)



@login_required
@customer_required
def start_session(request, vehicle_id):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=405)

    vehicle = get_object_or_404(Vehicle, id=vehicle_id)
    if vehicle.owner != request.user:
        raise Http404

    ticket = None
    try:
        with transaction.atomic():
            # Source of truth: a slot is free if no active ticket holds it
            held_slot_ids = Ticket.objects.filter(
                vehicle_exited=False,
                slot__isnull=False
            ).values_list('slot_id', flat=True)

            slot = (
                ParkingSlot.objects
                .exclude(id__in=held_slot_ids)
                .select_for_update()
                .first()
            )

            if not slot:
                messages.error(request, "No parking slots available.")
                return redirect("park_system:vehicles")

            # DB constraint is the real guard — is_occupied is now just a display hint
            ticket = Ticket.objects.create(
                vehicle=vehicle,
                slot=slot
            )
            log_action(
                action=AuditLog.Action.SESSION_STARTED,
                performed_by=request.user,
                ticket=ticket,
                vehicle=vehicle,
                metadata={
                    "slot_number": slot.slot_number,
                    "entry_time": str(ticket.entry_time),
                }
            )
            
    except IntegrityError as e:
        error = str(e)
        if "unique_active_ticket_per_vehicle" in error:
            messages.error(request, "Vehicle already has an active session.")
        elif "unique_active_ticket_per_slot" in error:
            messages.error(request, "Slot was just taken — please try again.")
        else:
            messages.error(request, "Could not start session. Please try again.")
        return redirect("park_system:vehicles")

    return redirect("park_system:ticket_detail", code=ticket.code)

@login_required
def ticket_detail(request, code):
    if request.user.profile.is_admin or request.user.profile.is_attendant:
        ticket = get_object_or_404(Ticket, code=code)
    else:
        ticket = get_object_or_404(Ticket, code=code,vehicle__owner=request.user)

    duration = None
    duration_display = None

    if ticket.exit_time:
        duration = ticket.exit_time - ticket.entry_time
        total_seconds = int(duration.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        if hours > 0:
            duration_display = f"{hours} hour(s) {minutes} minute(s)"
        else:
            duration_display = f"{minutes} minute(s)"

    return render(request, "park_system/ticket_detail.html", {
        "ticket": ticket,
        "duration": duration_display
    })

@login_required
def end_session(request, code):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405)

    if request.user.profile.is_admin or request.user.profile.is_attendant:
        ticket = get_object_or_404(Ticket, code=code)
    else:
        ticket = get_object_or_404(Ticket, code=code, vehicle__owner=request.user)

    if ticket.exit_time is not None:
        messages.error(request, "Session already closed")
        return redirect("park_system:tickets")

    try:
        with transaction.atomic():
            ticket.close_session()
            

            log_action(
                action=AuditLog.Action.SESSION_CLOSED,
                performed_by=request.user,
                ticket=ticket,
                vehicle=ticket.vehicle,
                metadata={
                    "exit_time": str(ticket.exit_time),
                    "amount": ticket.amount,
                    "slot": str(ticket.slot),
                    "duration_hours": round(
                        (ticket.exit_time - ticket.entry_time).total_seconds() / 3600, 2
                    ),
                }
            )

    except ValueError as e:
        messages.error(request, str(e))
        return redirect("park_system:tickets")

    schedule(
        'park_system.tasks.check_single_vehicle_flagged',
        ticket.vehicle.id,
        schedule_type='O',
        next_run=timezone.now() + timedelta(minutes=20)
    )

    return redirect("park_system:ticket_detail", code=ticket.code)

    

@login_required
def tickets(request):
    if request.user.profile.is_admin :
        tickets = Ticket.objects.all()
        active_tickets = Ticket.objects.filter( exit_time__isnull=True )

        closed_tickets = Ticket.objects.filter(exit_time__isnull=False)
    else:
        tickets = Ticket.objects.filter(vehicle__owner =request.user)
        active_tickets = Ticket.objects.filter(
            vehicle__owner=request.user,
            exit_time__isnull=True
        )

        closed_tickets = Ticket.objects.filter(
            vehicle__owner=request.user,
            exit_time__isnull=False
        )

    context = {
        "tickets": tickets,
        "active_tickets": active_tickets,
        "closed_tickets": closed_tickets
    }

    return render(request, "park_system/tickets.html", context)


@login_required
def delete_vehicle(request, vehicle_id):
    vehicle = get_object_or_404(Vehicle, id=vehicle_id)

    # Ensure only the owner can delete
    if vehicle.owner != request.user:
        raise Http404

    if request.method == "POST":
        log_action(
            action=AuditLog.Action.VEHICLE_DELETED,
            performed_by=request.user,
            vehicle=None,  # can't FK a deleted object
            metadata={
                "number_plate": vehicle.number_plate,
                "vehicle_type": vehicle.vehicle_type,
                "vehicle_make": vehicle.vehicle_make,
                "owner": vehicle.owner.username,
            }
        )
        vehicle.delete()
        return redirect('park_system:vehicles')

    return render(request, 'park_system/delete_vehicle.html', {'vehicle': vehicle})
    

@login_required
@role_required('admin', 'attendant')
def entry(request, vehicle_id):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405)

    vehicle = get_object_or_404(Vehicle, id=vehicle_id)

    try:
        with transaction.atomic():
            # Must have an active session before physical entry is allowed
            ticket = Ticket.objects.filter(
                vehicle=vehicle,
                exit_time__isnull=True
            ).select_related('slot').first()

            if not ticket:
                messages.error(request, "Start a session before entry.")
                return redirect("park_system:vehicles")

            if vehicle.is_active:
                messages.error(request, "Vehicle is already inside the park.")
                return redirect("park_system:vehicles")

            # Slot was assigned at session start — now physically occupy it
            if not ticket.slot:
                messages.error(request, "No slot assigned to this session. Contact admin.")
                return redirect("park_system:vehicles")

            ticket.slot.occupy_slot()

            vehicle.is_active = True
            vehicle.save()

            log_action(
                action=AuditLog.Action.VEHICLE_ACTIVATED,
                performed_by=request.user,
                vehicle=vehicle,
                metadata={
                    "number_plate": vehicle.number_plate,
                    "slot": str(ticket.slot),
                    "ticket_code": str(ticket.code),
                }
            )

    except ValueError as e:
        messages.error(request, str(e))
        return redirect("park_system:vehicles")

    return JsonResponse({
        "message": f"{vehicle.number_plate} entered — assigned to {ticket.slot}"
    })

@login_required
@role_required('admin', 'attendant')
def exit_park(request, vehicle_id):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405)

    vehicle = get_object_or_404(Vehicle, id=vehicle_id)

    # Block exit if session still active
    active_session_exists = Ticket.objects.filter(
        vehicle=vehicle,
        exit_time__isnull=True
    ).exists()

    if active_session_exists:
        messages.error(request, "Session is still active — close it first.")
        return redirect("park_system:vehicles")

    # Block exit if unpaid
    unpaid_exists = Ticket.objects.filter(
        vehicle=vehicle,
        exit_time__isnull=False,
        payment_status=False
    ).exists()

    if unpaid_exists:
        messages.error(request, "Unpaid ticket exists — collect payment before exit.")
        log_action(
            action=AuditLog.Action.VEHICLE_FLAGGED,
            performed_by=request.user,
            vehicle=vehicle,
            metadata={
                "reason": "Attempted exit with unpaid ticket",
                "number_plate": vehicle.number_plate,
            }
        )
        return redirect("park_system:vehicles")

    try:
        with transaction.atomic():
            # Get the last paid closed ticket to find the slot
            last_ticket = Ticket.objects.filter(
                vehicle=vehicle,
                exit_time__isnull=False,
                payment_status=True
            ).select_related('slot').order_by('-exit_time').first()

            if last_ticket and last_ticket.slot:
                Ticket.objects.filter(id=last_ticket.id).update(vehicle_exited=True)
                last_ticket.slot.free_slot()  # ← named method, not raw assignment

            vehicle.is_active = False
            vehicle.flagged = False
            vehicle.save()

            log_action(
                action=AuditLog.Action.VEHICLE_DEACTIVATED,
                performed_by=request.user,
                vehicle=vehicle,
                metadata={
                    "number_plate": vehicle.number_plate,
                    "slot_freed": str(last_ticket.slot) if last_ticket and last_ticket.slot else "none",
                }
            )

    except ValueError as e:
        messages.error(request, str(e))
        return redirect("park_system:vehicles")

    return JsonResponse({"message": f"{vehicle.number_plate} exited successfully"})


@login_required
@admin_required
def admin_dashboard(request):
    total_vehicles = Vehicle.objects.count()
    total_slots = ParkingSlot.objects.count()

    occupied_slot_ids = Ticket.objects.filter(exit_time__isnull=True).values_list('slot_id', flat=True)
    occupied_slots = len(occupied_slot_ids)

    active_sessions = Ticket.objects.filter(exit_time__isnull=True).count()

    total_revenue = Ticket.objects.filter(exit_time__isnull=False,payment_status=True).aggregate(total=Sum('amount'))['total'] or 0

    unpaid_closed = Ticket.objects.filter(
        exit_time__isnull=False,
        payment_status=False
    ).select_related('vehicle').order_by('-exit_time')

    unpaid_revenue_at_risk = unpaid_closed.aggregate( total=Sum('amount'))['total'] or 0

    flagged_vehicles = Vehicle.objects.filter(flagged=True).order_by('number_plate')

    overdue_tickets = Ticket.objects.filter(
        exit_time__isnull=True,
        overdue=True
    ).select_related('vehicle', 'slot').order_by('overdue_since')

    
    recent_logs = AuditLog.objects.select_related(
        'performed_by', 'vehicle', 'ticket'
    ).order_by('-timestamp')[:10]

    return render(request, "park_system/admin_dashboard.html", {
        "total_vehicles": total_vehicles,
        "total_slots": total_slots,
        "occupied_slots": occupied_slots,
        "free_slots": total_slots - occupied_slots,
        "active_sessions": active_sessions,
        "total_revenue": total_revenue,
        "unpaid_closed": unpaid_closed,
        "unpaid_revenue_at_risk": unpaid_revenue_at_risk,
        "flagged_vehicles": flagged_vehicles,
        "overdue_tickets": overdue_tickets,
        "recent_logs": recent_logs,
    })

@login_required
@role_required('admin', 'attendant')
def attendant_add_vehicle(request):
    if request.method == "POST":
        form = AttendantVehicleForm(request.POST)
        if form.is_valid():
            vehicle = form.save(commit=False)
            # owner is selected in the form — no need to set it manually
            vehicle.save()
            log_action(
                action=AuditLog.Action.VEHICLE_ADDED,
                performed_by=request.user,
                vehicle=vehicle,
                metadata={
                    "number_plate": vehicle.number_plate,
                    "vehicle_type": vehicle.vehicle_type,
                    "vehicle_make": vehicle.vehicle_make,
                    "owner": vehicle.owner.username,
                    "added_by_role": request.user.profile.role,
                }
            )
            messages.success(request, f"Vehicle {vehicle.number_plate} added successfully.")
            return redirect("park_system:vehicles")
    else:
        form = AttendantVehicleForm()

    return render(request, "park_system/attendant_add_vehicle.html", {"form": form})


@login_required
@role_required('admin', 'attendant')
def attendant_start_session(request, vehicle_id):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=405)

    # attendants don't need to own the vehicle
    vehicle = get_object_or_404(Vehicle, id=vehicle_id)

    active_session_exists = Ticket.objects.filter(vehicle=vehicle, exit_time__isnull=True).exists()
    if active_session_exists:
        messages.error(request, "Vehicle already has an active session.")
        return redirect("park_system:vehicles")

    slot = None
    ticket = None
    try:
        with transaction.atomic():
            held_slot_ids = Ticket.objects.filter(
                vehicle_exited=False,
                slot__isnull=False
            ).values_list('slot_id', flat=True)

            slot = (
                ParkingSlot.objects
                .exclude(id__in=held_slot_ids)
                .select_for_update()
                .first()
            )

            if not slot:
                messages.error(request, "No parking slots available.")
                return redirect("park_system:vehicles")

            ticket = Ticket.objects.create(
                vehicle=vehicle,
                slot=slot
            )

            log_action(
                action=AuditLog.Action.SESSION_STARTED,
                performed_by=request.user,
                ticket=ticket,
                vehicle=vehicle,
                metadata={
                    "slot_number": slot.slot_number,
                    "entry_time": str(ticket.entry_time),
                }
            )

    except IntegrityError as e:
        error = str(e)
        if "unique_active_ticket_per_vehicle" in error:
            messages.error(request, "Vehicle already has an active session.")
        elif "unique_active_ticket_per_slot" in error:
            messages.error(request, "Slot was just taken — please try again.")
        else:
            messages.error(request, "Could not start session. Please try again.")
        return redirect("park_system:vehicles")

    return redirect("park_system:ticket_detail", code=ticket.code)


@login_required
@role_required('admin', 'attendant')
def attendant_mark_paid(request, code):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405)

    ticket = get_object_or_404(Ticket, code=code)

    try:
        ticket.mark_paid()
        messages.success(request, "Payment recorded successfully.")
        log_action(
            action=AuditLog.Action.PAYMENT_RECORDED,
            performed_by=request.user,
            ticket=ticket,
            vehicle=ticket.vehicle,
            metadata={
                "amount": ticket.amount,
                "paid_at": str(ticket.paid_at),
            }
        )
    except ValueError as e:
        messages.error(request, str(e))
    

    return redirect("park_system:ticket_detail", code=ticket.code)

@login_required
@customer_required
def mark_paid(request, code):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405)

    ticket = get_object_or_404(Ticket, code=code, vehicle__owner=request.user)

    try:
        ticket.mark_paid()
        messages.success(request, "Payment recorded successfully.")
        log_action(
            action=AuditLog.Action.PAYMENT_RECORDED,
            performed_by=request.user,
            ticket=ticket,
            vehicle=ticket.vehicle,
            metadata={
                "amount": ticket.amount,
                "paid_at": str(ticket.paid_at),
            }
        )
    except ValueError as e:
        messages.error(request, str(e))

    return redirect("park_system:ticket_detail", code=ticket.code)
    



@login_required
@admin_required
def register_attendant(request):
    if request.method == "POST":
        form = AttendantRegistrationForm(request.POST)
        if form.is_valid():
            # create the user
            user = User.objects.create_user(
                username=form.cleaned_data['username'],
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password']
            )
            # set their role to attendant
            user.profile.role = UserProfile.Role.ATTENDANT
            user.profile.save()

            messages.success(request, f"Attendant {user.username} created successfully.")
            return redirect("park_system:register_attendant")
    else:
        form = AttendantRegistrationForm()

    return render(request, "park_system/register_attendant.html", {"form": form})

@login_required
@admin_required
def audit_logs(request):
    logs = AuditLog.objects.select_related(
        'performed_by', 'ticket', 'vehicle'
    )

    # optional filters from query params
    action = request.GET.get('action')
    user_id = request.GET.get('user')

    if action:
        logs = logs.filter(action=action)
    if user_id:
        logs = logs.filter(performed_by_id=user_id)

    return render(request, "park_system/audit_logs.html", {
        "logs": logs,
        "actions": AuditLog.Action.choices,
    })


@login_required
@admin_required
def reopen_ticket(request, code):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405)

    ticket = get_object_or_404(Ticket, code=code)

    try:
        with transaction.atomic():
            old_exit_time = ticket.exit_time
            old_amount = ticket.amount
            old_slot = ticket.slot

            ticket.reopen_session()  # clears exit_time and amount

            # Check if original slot is still free
            original_slot_available = False
            if old_slot:
                original_slot_taken = Ticket.objects.filter(
                    slot=old_slot,
                    exit_time__isnull=True
                ).exclude(id=ticket.id).exists()
                original_slot_available = not original_slot_taken

            if original_slot_available:
                # Keep original slot, just mark it occupied
                slot = ParkingSlot.objects.select_for_update().get(id=old_slot.id)
                slot.is_occupied = True
                slot.save()
                reassigned = False

            else:
                # Original slot is taken — find a new one
                occupied_slot_ids = Ticket.objects.filter(
                    exit_time__isnull=True
                ).values_list('slot_id', flat=True)

                new_slot = (
                    ParkingSlot.objects
                    .exclude(id__in=occupied_slot_ids)
                    .select_for_update()
                    .first()
                )

                if not new_slot:
                    raise ValueError("No slots available to reopen this ticket")

                # Update ticket's slot directly — bypass save() guard
                Ticket.objects.filter(id=ticket.id).update(slot=new_slot)
                new_slot.is_occupied = True
                new_slot.save()
                ticket.refresh_from_db()
                reassigned = True

            log_action(
                action=AuditLog.Action.TICKET_REOPENED,
                performed_by=request.user,
                ticket=ticket,
                vehicle=ticket.vehicle,
                metadata={
                    "number_plate": ticket.vehicle.number_plate,
                    "old_exit_time": str(old_exit_time),
                    "old_amount": old_amount,
                    "original_slot": str(old_slot),
                    "new_slot": str(ticket.slot),
                    "slot_reassigned": reassigned,
                    "reason": request.POST.get("reason", "No reason provided"),
                }
            )

    except ValueError as e:
        messages.error(request, str(e))
        return redirect("park_system:ticket_detail", code=code)

    if reassigned:
        messages.success(
            request,
            f"Ticket reopened. Original slot was taken — reassigned to {ticket.slot}."
        )
    else:
        messages.success(request, "Ticket reopened. Original slot retained.")

    return redirect("park_system:ticket_detail", code=ticket.code)

@login_required
@role_required('admin', 'attendant')
def register_customer(request):
    if request.method == "POST":
        form = CustomerRegistrationForm(request.POST)
        if form.is_valid():
            # Create the user
            user = User.objects.create_user(
                username=form.cleaned_data['username'],
                email=form.cleaned_data.get('email', ''),
                password=form.cleaned_data['password'],
                first_name=form.cleaned_data.get('first_name', ''),
                last_name=form.cleaned_data.get('last_name', ''),
            )

            # Profile is auto-created by the signal with role=CUSTOMER
            # No need to set role manually — customer is the default

            log_action(
                action=AuditLog.Action.CUSTOMER_REGISTERED,
                performed_by=request.user,
                metadata={
                    "new_user": user.username,
                    "email": user.email,
                    "registered_by": request.user.username,
                    "registered_by_role": request.user.profile.role,
                }
            )

            messages.success(
                request,
                f"Customer account created for {user.username}. They can now log in."
            )
            return redirect("park_system:register_customer")
    else:
        form = CustomerRegistrationForm()

    return render(request, "park_system/register_customer.html", {"form": form})



@login_required
@customer_required
def initiate_payment(request, code):
    """Customer clicks Pay on their ticket detail page."""
    ticket = get_object_or_404(Ticket, code=code, vehicle__owner=request.user)

    if ticket.payment_status:
        messages.error(request, "This ticket is already paid.")
        return redirect("park_system:ticket_detail", code=code)

    if ticket.exit_time is None:
        messages.error(request, "Session must be closed before payment.")
        return redirect("park_system:ticket_detail", code=code)

    if not request.user.email:
        messages.error(request, "Your account has no email address. Contact the attendant.")
        return redirect("park_system:ticket_detail", code=code)

    reference = generate_reference()
    callback_url = request.build_absolute_uri(
    reverse('park_system:payment_callback', kwargs={'reference': reference})
)

    response = initialize_transaction(
        email=request.user.email,
        amount_naira=ticket.amount,
        reference=reference,
        callback_url=callback_url,
        metadata={
            "ticket_code": str(ticket.code),
            "vehicle": ticket.vehicle.number_plate,
            "initiated_by": request.user.username,
        }
    )

    if not response.get("status"):
        messages.error(request, "Could not connect to payment gateway. Try again.")
        return redirect("park_system:ticket_detail", code=code)

    # Create a pending Payment record before redirecting
    Payment.objects.create(
        ticket=ticket,
        initiated_by=request.user,
        reference=reference,
        amount=ticket.amount * 100,  # store in kobo
        status=Payment.Status.PENDING,
        meta=response
    )

    log_action(
        action=AuditLog.Action.PAYMENT_INITIATED,
        performed_by=request.user,
        ticket=ticket,
        vehicle=ticket.vehicle,
        metadata={
            "reference": reference,
            "amount": ticket.amount,
            "channel": "customer_self_service",
        }
    )

    # Redirect customer to Paystack payment page
    return redirect(response["data"]["authorization_url"])


# ------------------------------------------------------------------ #
# ATTENDANT — initiates payment on behalf of customer                #
# ------------------------------------------------------------------ #

@login_required
@role_required('admin', 'attendant')
def attendant_initiate_payment(request, code):
    """Attendant triggers payment — sends customer to Paystack."""
    ticket = get_object_or_404(Ticket, code=code)

    if ticket.payment_status:
        messages.error(request, "This ticket is already paid.")
        return redirect("park_system:ticket_detail", code=code)

    if ticket.exit_time is None:
        messages.error(request, "Close the session before initiating payment.")
        return redirect("park_system:ticket_detail", code=code)

    customer_email = ticket.vehicle.owner.email
    if not customer_email:
        messages.error(request, "Customer has no email on record. Use manual payment.")
        return redirect("park_system:ticket_detail", code=code)

    reference = generate_reference()
    callback_url = request.build_absolute_uri(
    reverse('park_system:payment_callback', kwargs={'reference': reference})
)

    response = initialize_transaction(
        email=customer_email,
        amount_naira=ticket.amount,
        reference=reference,
        callback_url=callback_url,
        metadata={
            "ticket_code": str(ticket.code),
            "vehicle": ticket.vehicle.number_plate,
            "initiated_by": request.user.username,
            "attendant": True,
        }
    )

    if not response.get("status"):
        messages.error(request, "Could not connect to payment gateway. Try again.")
        return redirect("park_system:ticket_detail", code=code)

    Payment.objects.create(
        ticket=ticket,
        initiated_by=request.user,
        reference=reference,
        amount=ticket.amount * 100,
        status=Payment.Status.PENDING,
        meta=response
    )

    log_action(
        action=AuditLog.Action.PAYMENT_INITIATED,
        performed_by=request.user,
        ticket=ticket,
        vehicle=ticket.vehicle,
        metadata={
            "reference": reference,
            "amount": ticket.amount,
            "channel": "attendant_initiated",
            "customer_email": customer_email,
        }
    )

    return redirect(response["data"]["authorization_url"])


# ------------------------------------------------------------------ #
# CALLBACK — customer returns here after paying on Paystack          #
# ------------------------------------------------------------------ #

def payment_callback(request, reference):
    """
    Paystack redirects customer here after payment attempt.
    Always verify server-to-server — never trust URL parameters.
    """
    response = verify_transaction(reference)

    if not response.get("status"):
        messages.error(request, "Could not verify payment. Contact support.")
        return redirect("park_system:tickets")

    data = response["data"]
    paystack_status = data.get("status")  # success, failed, abandoned

    try:
        payment = Payment.objects.get(reference=reference)
    except Payment.DoesNotExist:
        messages.error(request, "Payment record not found.")
        return redirect("park_system:tickets")

    ticket = payment.ticket

    if paystack_status == "success" and payment.status != Payment.Status.SUCCESS:
        with transaction.atomic():
            # Update Payment record
            Payment.objects.filter(reference=reference).update(
                status=Payment.Status.SUCCESS,
                paystack_id=data.get("id"),
                channel=data.get("channel"),
                paid_at=timezone.now(),
                meta=data
            )

            # Mark ticket as paid
            ticket.mark_paid()

            log_action(
                action=AuditLog.Action.PAYMENT_RECORDED,
                performed_by=request.user if request.user.is_authenticated else None,
                ticket=ticket,
                vehicle=ticket.vehicle,
                metadata={
                    "reference": reference,
                    "channel": data.get("channel"),
                    "amount": payment.amount_naira,
                    "source": "callback",
                }
            )

        messages.success(request, f"Payment of ₦{payment.amount_naira} confirmed. Thank you.")

    elif paystack_status == "abandoned":
        Payment.objects.filter(reference=reference).update(
            status=Payment.Status.ABANDONED,
            meta=data
        )
        log_action(
            action=AuditLog.Action.PAYMENT_FAILED,
            performed_by=None,
            ticket=ticket,
            vehicle=ticket.vehicle,
            metadata={"reference": reference, "reason": "abandoned"}
        )
        messages.warning(request, "Payment was not completed.")

    else:
        Payment.objects.filter(reference=reference).update(
            status=Payment.Status.FAILED,
            meta=data
        )
        log_action(
            action=AuditLog.Action.PAYMENT_FAILED,
            performed_by=None,
            ticket=ticket,
            vehicle=ticket.vehicle,
            metadata={"reference": reference, "reason": paystack_status}
        )
        messages.error(request, "Payment failed. Please try again.")

    return redirect("park_system:ticket_detail", code=ticket.code)


# ------------------------------------------------------------------ #
# WEBHOOK — Paystack confirms payment server-to-server               #
# ------------------------------------------------------------------ #

from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def paystack_webhook(request):
    """
    Paystack POSTs here when a payment event occurs.
    This is the reliable confirmation — callback can be skipped if
    customer closes their browser.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    # Verify the request is genuinely from Paystack
    paystack_signature = request.headers.get("x-paystack-signature")
    if not paystack_signature:
        return JsonResponse({"error": "No signature"}, status=400)

    computed = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode('utf-8'),
        request.body,
        hashlib.sha512
    ).hexdigest()

    if computed != paystack_signature:
        return JsonResponse({"error": "Invalid signature"}, status=400)

    import json
    payload = json.loads(request.body)
    event = payload.get("event")
    data = payload.get("data", {})
    reference = data.get("reference")

    if event == "charge.success":
        try:
            payment = Payment.objects.get(reference=reference)
        except Payment.DoesNotExist:
            return JsonResponse({"status": "ok"})  # not our payment, ignore

        if payment.status != Payment.Status.SUCCESS:
            with transaction.atomic():
                Payment.objects.filter(reference=reference).update(
                    status=Payment.Status.SUCCESS,
                    paystack_id=data.get("id"),
                    channel=data.get("channel"),
                    paid_at=timezone.now(),
                    meta=data
                )

                ticket = payment.ticket
                if not ticket.payment_status:
                    ticket.mark_paid()

                    log_action(
                        action=AuditLog.Action.PAYMENT_RECORDED,
                        performed_by=None,  # system confirmed, no user
                        ticket=ticket,
                        vehicle=ticket.vehicle,
                        metadata={
                            "reference": reference,
                            "channel": data.get("channel"),
                            "amount": payment.amount_naira,
                            "source": "webhook",
                        }
                    )

    # Always return 200 to Paystack — even if we did nothing
    return JsonResponse({"status": "ok"})