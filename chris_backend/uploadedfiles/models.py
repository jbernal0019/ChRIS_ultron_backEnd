
from django.db import models
import django_filters
from django_filters.rest_framework import FilterSet


def uploaded_file_path(instance, filename):
    # file will be stored to Swift at:
    # SWIFT_CONTAINER_NAME/<username>/uploads/<upload_path>
    return instance.owner.upload_path


class UploadedFile(models.Model):
    creation_date = models.DateTimeField(auto_now_add=True)
    fname = models.FileField(max_length=512, upload_to=uploaded_file_path, unique=True)
    owner = models.ForeignKey('auth.User', on_delete=models.CASCADE)

    class Meta:
        ordering = ('-fname',)

    def __str__(self):
        return self.fname.name


class UploadedFileFilter(FilterSet):
    min_creation_date = django_filters.DateFilter(field_name='creation_date',
                                                  lookup_expr='gte')
    max_creation_date = django_filters.DateFilter(field_name='creation_date',
                                                 lookup_expr='lte')
    fname = django_filters.CharFilter(field_name='fname', lookup_expr='startswith')
    fname_exact = django_filters.CharFilter(field_name='fname', lookup_expr='exact')
    owner_username = django_filters.CharFilter(field_name='owner__username',
                                               lookup_expr='exact')

    class Meta:
        model = UploadedFile
        fields = ['id', 'min_creation_date', 'max_creation_date', 'fname', 'fname_exact',
                  'owner_username']
