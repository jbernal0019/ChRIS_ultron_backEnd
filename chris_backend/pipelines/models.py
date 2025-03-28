
import logging
from collections import deque

from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

import django_filters
from django_filters.rest_framework import FilterSet

from core.models import ChrisFolder, ChrisFile
from core.storage import connect_storage
from plugins.models import Plugin, PluginParameter


logger = logging.getLogger(__name__)


PIPELINE_SOURCE_FILE_TYPE_CHOICES = [('yaml', 'YAML file'), ('json', 'JSON file')]


class Pipeline(models.Model):
    creation_date = models.DateTimeField(auto_now_add=True)
    modification_date = models.DateTimeField(auto_now_add=True)
    name = models.CharField(max_length=100, unique=True)
    locked = models.BooleanField(default=True)
    authors = models.CharField(max_length=200, blank=True)
    category = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=800, blank=True)
    owner = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    plugins = models.ManyToManyField(Plugin, related_name='pipelines',
                                     through='PluginPiping')

    class Meta:
        ordering = ('category',)

    def __str__(self):
        return self.name

    def get_pipings_parameters_names(self):
        """
        Custom method to get the list of all the plugin parameter names for all the
        associated plugin pipings. The name of the parameters is transformed to have the
        plugin id, piping id and previous piping id as a prefix.
        """
        pipings = self.plugin_pipings.all()
        param_names = []
        for pip in pipings:
            plg = pip.plugin
            prev_pip_id = pip.previous.id if pip.previous else 'null'
            # param name becomes <plugin.id>_<piping.id>_<previous_piping.id>_<param.name>
            param_names.extend(['%s_%s_%s_%s' % (plg.id, pip.id, prev_pip_id, param.name)
                                for param in plg.parameters.all()])
        return param_names

    def get_default_parameters(self):
        """
        Custom method to get the list of all the pipeline's plugin parameter default
        values regardless of their type.
        """
        pipeline_default_parameters = []
        pipeline_default_parameters.extend(list(DefaultPipingStrParameter.objects.filter(
            plugin_piping__pipeline=self)))
        pipeline_default_parameters.extend(list(DefaultPipingIntParameter.objects.filter(
            plugin_piping__pipeline=self)))
        pipeline_default_parameters.extend(
            list(DefaultPipingFloatParameter.objects.filter(
                plugin_piping__pipeline=self)))
        pipeline_default_parameters.extend(list(DefaultPipingBoolParameter.objects.filter(
            plugin_piping__pipeline=self)))
        return pipeline_default_parameters

    def get_pipings_tree(self):
        """
        Custom method to return a dictionary containing a dictionary representing a tree
        of pipings and the id of the piping that is the root of the tree. The keys of the
        dictionary tree are the pipings' ids and the values are dictionaries containing
        the piping and the list of child pipings' ids.
        """
        pipings = list(self.plugin_pipings.all())
        root_pip = [pip for pip in pipings if not pip.previous][0]
        root_id = root_pip.id
        tree = {}
        tree[root_id] = {'piping': root_pip, 'child_ids':[]}
        for pip in pipings:
            if pip.id not in tree:
                tree[pip.id] = {'piping': pip, 'child_ids': []}
                prev_id = pip.previous.id
                if prev_id in tree:
                    tree[prev_id]['child_ids'].append(pip.id)
                else:
                    tree[prev_id] = {'piping': pip.previous, 'child_ids': [pip.id]}
        return {'root_id': root_id, 'tree': tree}

    def check_parameter_defaults(self):
        """
        Custom method to raise an exception if any of the plugin parameters associated to
        any of the pipings in the pipeline doesn't have a default value.
        """
        for piping in self.plugin_pipings.all():
            piping.check_parameter_defaults()

    def get_plugin_tree(self):
        """
        Custom method to return a list representation of the pipeline's plugin_tree.
        """
        pipeline_default_parameters = self.get_default_parameters()

        tree_nodes_dict = {}
        id_to_title = {}  # mapping from piping id to piping title
        is_ts_dict = {}
        for default_param in pipeline_default_parameters:
            piping = default_param.plugin_piping
            previous = piping.previous

            if piping.title not in tree_nodes_dict:
                tree_nodes_dict[piping.title] = {
                    'plugin_id': piping.plugin.id,
                    'previous': previous.title if previous is not None else None,
                    'title': piping.title,
                    'plugin_parameter_defaults': []
                }
                id_to_title[piping.id] = piping.title
                is_ts_dict[piping.title] = piping.plugin.meta.type == 'ts'

            tree_nodes_dict[piping.title]['plugin_parameter_defaults'].append(
                {
                    'name': default_param.plugin_param.name,
                    'default': default_param.value
                }
            )

        root_title = [pip_title for pip_title in tree_nodes_dict.keys()
                      if tree_nodes_dict[pip_title]['previous'] is None][0]
        tree = {root_title: []}  # dict with list of child titles for each piping title
        for title in tree_nodes_dict.keys():
            if is_ts_dict[title]:  # process 'ts' plugins
                for default_d in tree_nodes_dict[title]['plugin_parameter_defaults']:
                    if default_d['name'] == 'plugininstances':
                        default = default_d['default']
                        if default:
                            parent_titles = [id_to_title[int(pip_id)] for pip_id in
                                             default.split(',')]
                            default_d['default'] = ','.join(parent_titles)
                        break
            if title not in tree:
                tree[title] = []
                prev_title = tree_nodes_dict[title]['previous']
                if prev_title in tree:
                    tree[prev_title].append(title)
                else:
                    tree[prev_title] = [title]

        plugin_tree = [tree_nodes_dict[root_title]]
        # breath-first traversal of tree to order nodes in the returned plugin_tree
        queue = deque(tree[root_title])
        while len(queue):
            curr_title = queue.popleft()
            plugin_tree.append(tree_nodes_dict[curr_title])
            queue.extend(tree[curr_title])

        return plugin_tree

    @staticmethod
    def get_accesible_pipelines(user):
        """
        Custom method to get a filtered queryset with all the pipelines that are
        accessible to a given user (not locked or otherwise own by the user).
        """
        queryset = Pipeline.objects.all()
        if user.is_authenticated:
            # if the user is chris then return all the pipelines in the queryset
            if user.username == 'chris':
                return queryset
            # construct the full lookup expression.
            lookup = models.Q(locked=False) | models.Q(owner=user)
        else:
            lookup = models.Q(locked=False)
        return queryset.filter(lookup)


class PipelineFilter(FilterSet):
    min_creation_date = django_filters.IsoDateTimeFilter(field_name="creation_date",
                                                         lookup_expr='gte')
    max_creation_date = django_filters.IsoDateTimeFilter(field_name="creation_date",
                                                         lookup_expr='lte')
    owner_username = django_filters.CharFilter(field_name='owner__username',
                                               lookup_expr='exact')
    name = django_filters.CharFilter(field_name='name', lookup_expr='icontains')
    name_exact = django_filters.CharFilter(field_name='name', lookup_expr='exact')
    category = django_filters.CharFilter(field_name='category', lookup_expr='icontains')
    description = django_filters.CharFilter(field_name='description',
                                            lookup_expr='icontains')
    authors = django_filters.CharFilter(field_name='authors', lookup_expr='icontains')

    class Meta:
        model = Pipeline
        fields = ['id', 'owner_username', 'name', 'name_exact', 'category', 'description',
                  'authors', 'min_creation_date', 'max_creation_date']


class PipelineSourceFile(ChrisFile):

    class Meta:
        ordering = ('-fname',)
        proxy = True

    @classmethod
    def get_base_queryset(cls):
        """
        Custom method to return a queryset that is only comprised by the files
        in the PIPELINES space tree.
        """
        return cls.objects.filter(fname__startswith='PIPELINES/')

@receiver(post_delete, sender=PipelineSourceFile)
def auto_delete_file_from_storage(sender, instance, **kwargs):
    storage_path = instance.fname.name
    storage_manager = connect_storage(settings)
    try:
        if storage_manager.obj_exists(storage_path):
            storage_manager.delete_obj(storage_path)
    except Exception as e:
        logger.error('Storage error, detail: %s' % str(e))


class PipelineSourceFileFilter(FilterSet):
    min_creation_date = django_filters.IsoDateTimeFilter(field_name='creation_date',
                                                         lookup_expr='gte')
    max_creation_date = django_filters.IsoDateTimeFilter(field_name='creation_date',
                                                         lookup_expr='lte')
    fname = django_filters.CharFilter(field_name='fname', lookup_expr='startswith')
    fname_exact = django_filters.CharFilter(field_name='fname', lookup_expr='exact')
    fname_icontains = django_filters.CharFilter(field_name='fname',
                                                lookup_expr='icontains')
    pipeline_id = django_filters.CharFilter(field_name='meta__pipeline_id',
                                            lookup_expr='exact')
    pipeline_name = django_filters.CharFilter(field_name='meta__pipeline__name',
                                              lookup_expr='exact')
    uploader_username = django_filters.CharFilter(field_name='meta__uploader__username',
                                                  lookup_expr='exact')

    class Meta:
        model = PipelineSourceFile
        fields = ['id', 'min_creation_date', 'max_creation_date', 'fname', 'fname_exact',
                  'fname_icontains', 'pipeline_id', 'pipeline_name', 'uploader_username']


class PipelineSourceFileMeta(models.Model):
    type = models.CharField(choices=PIPELINE_SOURCE_FILE_TYPE_CHOICES, default='yaml',
                            max_length=8, blank=True)
    pipeline = models.OneToOneField(Pipeline, on_delete=models.CASCADE,
                                    related_name='source_file_meta')
    source_file = models.OneToOneField(PipelineSourceFile, on_delete=models.CASCADE,
                                    related_name='meta')
    uploader = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL,
                                 related_name='uploaded_pipeline_source_file_metas')

    class Meta:
        ordering = ('uploader',)

    def __str__(self):
        return self.id


@receiver(post_delete, sender=PipelineSourceFileMeta)
def auto_delete_source_file_with_meta(sender, instance, **kwargs):
    try:
        instance.source_file.delete()
    except Exception:
        pass


class PluginPiping(models.Model):
    title = models.CharField(max_length=100)
    plugin = models.ForeignKey(Plugin, on_delete=models.CASCADE)
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE,
                                 related_name='plugin_pipings')
    previous = models.ForeignKey("self", on_delete=models.CASCADE, null=True,
                                 related_name='next')

    class Meta:
        ordering = ('pipeline',)
        unique_together = ('title', 'pipeline',)

    def __str__(self):
        return str(self.title)

    def save(self, *args, **kwargs):
        """
        Overriden to save the default plugin parameters' values associated with this
        piping.
        """
        param_defaults = []
        if 'parameter_defaults' in kwargs:
            param_defaults = kwargs['parameter_defaults']
            del kwargs['parameter_defaults']
        super(PluginPiping, self).save(*args, **kwargs)
        plugin = self.plugin
        parameters = plugin.parameters.all()
        for parameter in parameters:
            param = [d for d in param_defaults if d['name'] == parameter.name]
            default_model_class = DEFAULT_PIPING_PARAMETER_MODELS[parameter.type]
            try:
                default_piping_param = default_model_class.objects.get(
                    plugin_piping=self, plugin_param=parameter)
            except ObjectDoesNotExist:
                default_piping_param = default_model_class()
                default_piping_param.plugin_piping = self
                default_piping_param.plugin_param = parameter
                if param:
                    default_piping_param.value = param[0]['default']
                else:
                    # use plugin parameter's default for piping's default
                    default = parameter.get_default()
                    default_piping_param.value = default.value if default else None
                default_piping_param.save()
            else:
                if param:
                    default_piping_param.value = param[0]['default']
                    default_piping_param.save()

    def check_parameter_defaults(self):
        """
        Custom method to raise an exception if any of the plugin parameters associated to
        the piping doesn't have a default value.
        """
        for type in DEFAULT_PIPING_PARAMETER_MODELS:
            typed_parameters = getattr(self, type + '_param')
            for parameter in typed_parameters.all():
                if parameter.value is None:
                    raise ValueError('A default is required for parameter %s'
                                     % parameter.plugin_param.name)


class DefaultPipingStrParameter(models.Model):
    value = models.CharField(max_length=600, null=True, blank=True)
    plugin_piping = models.ForeignKey(PluginPiping, on_delete=models.CASCADE,
                                    related_name='string_param')
    plugin_param = models.ForeignKey(PluginParameter, on_delete=models.CASCADE,
                                     related_name='string_piping_default')

    class Meta:
        unique_together = ('plugin_piping', 'plugin_param',)

    def __str__(self):
        return self.value


class DefaultPipingIntParameter(models.Model):
    value = models.IntegerField(null=True)
    plugin_piping = models.ForeignKey(PluginPiping, on_delete=models.CASCADE,
                                    related_name='integer_param')
    plugin_param = models.ForeignKey(PluginParameter, on_delete=models.CASCADE,
                                     related_name='integer_piping_default')

    class Meta:
        unique_together = ('plugin_piping', 'plugin_param',)

    def __str__(self):
        return str(self.value)


class DefaultPipingFloatParameter(models.Model):
    value = models.FloatField(null=True)
    plugin_piping = models.ForeignKey(PluginPiping, on_delete=models.CASCADE,
                                    related_name='float_param')
    plugin_param = models.ForeignKey(PluginParameter, on_delete=models.CASCADE,
                                     related_name='float_piping_default')

    class Meta:
        unique_together = ('plugin_piping', 'plugin_param',)

    def __str__(self):
        return str(self.value)


class DefaultPipingBoolParameter(models.Model):
    value = models.BooleanField(null=True)
    plugin_piping = models.ForeignKey(PluginPiping, on_delete=models.CASCADE,
                                    related_name='boolean_param')
    plugin_param = models.ForeignKey(PluginParameter, on_delete=models.CASCADE,
                                     related_name='boolean_piping_default')

    class Meta:
        unique_together = ('plugin_piping', 'plugin_param',)

    def __str__(self):
        return str(self.value)


DEFAULT_PIPING_PARAMETER_MODELS = {'string': DefaultPipingStrParameter,
                                   'integer': DefaultPipingIntParameter,
                                   'float': DefaultPipingFloatParameter,
                                   'boolean': DefaultPipingBoolParameter}
