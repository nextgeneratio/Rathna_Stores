"""
URL configuration for Rathna_Stores project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # Public cake catalogue — root of the site
    path("", include("cakes.urls", namespace="cakes")),

    # Guest cart
    path("cart/", include("cart.urls", namespace="cart")),

    # Staff product management interface
    path("manage/", include("admin.urls", namespace="store_admin")),

    # Django built-in admin (auth/user management)
    path("django-admin/", admin.site.urls),

    # Django auth (login / logout)
    path("accounts/", include("django.contrib.auth.urls")),
]
