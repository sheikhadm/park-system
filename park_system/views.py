from django.shortcuts import render,redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from .models import Vehicle,Ticket
from .forms import VehicleForm
from django.http import HttpResponse
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

        ticket = Ticket.objects.create(vehicle=vehicle)
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
        return JsonResponse({"error": "Session already closed"}, status=400)

    ticket.close_session()

    duration = ticket.exit_time - ticket.entry_time
    minutes = duration.total_seconds() / 60
    return redirect("park_system:ticket_detail", code=ticket.code)
    