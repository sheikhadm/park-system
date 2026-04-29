from functools import wraps
from django.http import Http404
from django.shortcuts import redirect
from django.contrib import messages


def role_required(*roles):
    """
    Usage:
    @role_required('admin')
    @role_required('admin', 'attendant')
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            try:
                user_role = request.user.profile.role
            except Exception:
                raise Http404

            if user_role not in roles:
                messages.error(request, "You do not have permission to access this page.")
                return redirect('park_system:index')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def admin_required(view_func):
    return role_required('admin')(view_func)


def attendant_required(view_func):
    return role_required('admin', 'attendant')(view_func)


def customer_required(view_func):
    return role_required('customer')(view_func)