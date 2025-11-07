from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .auth import RegisterUserView, LoginView
from .automatico import RequestUDIDView, ValidateUDIDView, GetSubscriberInfoView, RevokeUDIDView, ListUDIDRequestsView
from .views import RequestUDIDManualView, ValidateAndAssociateUDIDView, AuthenticateWithUDIDView, DisassociateUDIDView,ValidateStatusUDIDView,ListSubscribersWithUDIDView, MetricsDashboardView

urlpatterns = [
    #* Automatic UDID management
    # Note: The automatic UDID management views are implemented in the automatico module.
    path('request-udid/', RequestUDIDView.as_view(), name='request-udid'),
    path('validate-udid/', ValidateUDIDView.as_view(), name='validate-udid'),
    path('get-subscriber-info/', GetSubscriberInfoView.as_view(), name='get-subscriber-info'),
    path('revoke-udid/', RevokeUDIDView.as_view(), name='revoke-udid'),
    path('udid-requests/', ListUDIDRequestsView.as_view(), name='list-udid-requests'),
    
    #* Manual UDID management
    # Note: The manual UDID management views are not implemented yet.
    path('request-udid-manual/', RequestUDIDManualView.as_view(), name='request-udid-manual'),
    path('validate-and-associate-udid/', ValidateAndAssociateUDIDView.as_view(), name='validate-and-associate-udid'),
    path('authenticate-with-udid/', AuthenticateWithUDIDView.as_view(), name='authenticate-with-udid'),
    path('validate/', ValidateStatusUDIDView.as_view(), name='validate_udid'),
    path('disassociate-udid/', DisassociateUDIDView.as_view(), name='disassociate-udid'),
    
    #* Public UDID management
    # Note: The public UDID management views are implemented in the views module.
    path('subscriberinfo/',ListSubscribersWithUDIDView.as_view(), name='list-subscribers'),
    
    #* Funciones de authentication
    # Note: The authentication views are implemented in the auth module.
    path('auth/register/', RegisterUserView.as_view(), name='register'),
    path('auth/login/', LoginView.as_view(), name='login'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    #* Métricas del sistema (para pruebas y monitoreo)
    path('metrics/', MetricsDashboardView.as_view(), name='metrics-dashboard'),
]
