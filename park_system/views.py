from django.shortcuts import render,redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, JsonResponse
from .models import Vehicle,Ticket,ParkingSlot
from .forms import VehicleForm
from django.http import HttpResponse
from django.contrib import messages
# Create your views here.

def index(request):
    return render(request, 'park_system/index.html')

@login_required
def add_vehicle(request):
    if request.method == "POST":
        form = VehicleForm(request.POST)
        if form.is_valid():
            vehicle = form.save(commit = False)
            vehicle.owner = request.user
            vehicle.save()   
            return redirect("park_system:index")  
    else:
        form = VehicleForm()

    return render(request, "park_system/add_vehicle.html", {"form": form})

@login_required
def vehicles(request):
    vehicles = Vehicle.objects.filter(owner=request.user)
    context = { "vehicles": vehicles }
    return render(request, "park_system/vehicles.html", context)


@login_required
def start_session(request, vehicle_id):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=405)
    else:
        vehicle = get_object_or_404(Vehicle, id=vehicle_id)
        if vehicle.owner != request.user:
            raise Http404
        
        active_session_exists = Ticket.objects.filter(vehicle=vehicle,exit_time__isnull=True).exists()
        if active_session_exists:
            messages.error(request, "Vehicle already has an active session.")
            return redirect("park_system:vehicles")


        
        
        with transaction.atomic():
            slot = ParkingSlot.objects.select_for_update().filter(is_occupied=False).first()
            if not slot:
                messages.error(request, "No parking slots available.")
                return redirect("park_system:vehicles")
            

            slot.is_occupied = True
            slot.save()

            ticket = Ticket.objects.create(
                vehicle=vehicle,
                slot=slot
            )

        

        return redirect("park_system:ticket_detail", code=ticket.code)

@login_required
def ticket_detail(request, code):
   
    ticket = get_object_or_404(Ticket, code=code,vehicle__owner=request.user)

    duration = None
    if ticket.exit_time:
        duration = ticket.exit_time - ticket.entry_time

    return render(request, "park_system/ticket_detail.html", {
        "ticket": ticket,
        "duration": duration
    })

@login_required
def end_session(request, code):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405)

    ticket = get_object_or_404(Ticket, code=code,vehicle__owner=request.user)

    if ticket.exit_time is not None:
        messages.error(request,  "Session already closed")
        return redirect("park_system:tickets")


    ticket.close_session()

    duration = ticket.exit_time - ticket.entry_time
    minutes = duration.total_seconds() / 60
    if ticket.slot:
        ticket.slot.is_occupied = False
        ticket.slot.save()

    ticket.save()
    return redirect("park_system:ticket_detail", code=ticket.code)

@login_required
def tickets(request):
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
        vehicle.delete()
        return redirect('park_system:vehicles')

    return render(request, 'park_system/delete_vehicle.html', {'vehicle': vehicle})
    