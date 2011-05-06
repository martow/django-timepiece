from decimal import Decimal

from django import forms
from django.db.models import Q
from django.conf import settings

from django.contrib.auth import models as auth_models
from django.contrib.auth import forms as auth_forms
from django.core.urlresolvers import reverse
from django.utils.translation import ugettext_lazy as _

from timepiece.models import Project, Entry
from timepiece.fields import PendulumDateTimeField
from timepiece.widgets import PendulumDateTimeWidget, SecondsToHoursWidget
from datetime import datetime, timedelta

from timepiece import models as timepiece

from ajax_select.fields import AutoCompleteSelectMultipleField, \
                               AutoCompleteSelectField, \
                               AutoCompleteSelectWidget
                               
class CreatePersonForm(auth_forms.UserCreationForm):
    class Meta:
        model = auth_models.User
        fields = (
            "username", "first_name", "last_name", 
            "email", "is_active", "is_staff")


class EditPersonForm(auth_forms.UserChangeForm):
    password_one = forms.CharField(required=False, max_length=36, label=_(u'Password'),
                                widget=forms.PasswordInput(render_value=False))
    password_two = forms.CharField(required=False, max_length=36, label=_(u'Repeat Password'),
                                widget=forms.PasswordInput(render_value=False))
    class Meta:
        model = auth_models.User
        fields = (
            "username", "first_name", "last_name", 
            "email", "is_active", "is_staff"
        )
                        
    def clean(self):
        super(EditPersonForm, self).clean()
        password_one = self.cleaned_data.get('password_one', None)
        password_two = self.cleaned_data.get('password_two', None)
        if password_one and password_one != password_two:
            raise forms.ValidationError(_('Passwords Must Match.'))
        return self.cleaned_data
    
    def save(self, *args, **kwargs):
        commit = kwargs.get('commit', True)
        kwargs['commit'] = False
        instance = super(EditPersonForm, self).save(*args, **kwargs)
        password_one = self.cleaned_data.get('password_one', None)
        if password_one:
            instance.set_password(password_one)
        if commit:
            instance.save()
        return instance
        
class CharAutoCompleteSelectWidget(AutoCompleteSelectWidget):
    def value_from_datadict(self, data, files, name):
        return data.get(name, None)


class QuickSearchForm(forms.Form):
    quick_search = AutoCompleteSelectField(
        'quick_search',
        widget=CharAutoCompleteSelectWidget('quick_search'),
    )
    
    def clean_quick_search(self):
        item = self.cleaned_data['quick_search']
        if isinstance(item, timepiece.Project):
            return reverse('view_project', kwargs={
                'project_id': item.id,
            })
        elif isinstance(item, timepiece.Business,):
            return reverse('view_business', kwargs={
                'business': item.id,
            })
        elif isinstance(item, auth_models.User,):
            return reverse('view_person', kwargs={
                'person_id': item.id,
            })
        raise forms.ValidationError('Must be a User or Project')
    
    def save(self):
        return self.cleaned_data['quick_search']


class SearchForm(forms.Form):
    search = forms.CharField(required=False)


class AddUserToProjectForm(forms.Form):
    user = AutoCompleteSelectField('user')
    
    def save(self):
        return self.cleaned_data['user']


class ClockInForm(forms.ModelForm):
    class Meta:
        model = timepiece.Entry
        fields = ('location', 'project', 'activity', 'start_time',)

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        default_loc = getattr(
            settings,
            'TIMEPIECE_DEFAULT_LOCATION_SLUG',
            None,
        )
        if default_loc:
            try:
                loc = timepiece.Location.objects.get(slug=default_loc)
            except timepiece.Location.DoesNotExist:
                loc = None
            if loc:
                initial = kwargs.get('initial', {})
                initial['location'] = loc.pk
        super(ClockInForm, self).__init__(*args, **kwargs)
        self.fields['start_time'].required = False
        self.fields['start_time'].initial = datetime.now()
        self.fields['start_time'].widget = forms.SplitDateTimeWidget(
            attrs={'class': 'timepiece-time'},
            date_format='%m/%d/%Y',
        )
        self.fields['project'].queryset = timepiece.Project.objects.filter(
            users=self.user,
        ).filter(
            Q(status__enable_timetracking=True) |
            Q(type__enable_timetracking=True)
        )
    
    def save(self, commit=True):
        entry = super(ClockInForm, self).save(commit=False)
        entry.hours = 0
        entry.clock_in(self.user, self.cleaned_data['project'])
        if commit:
            entry.save()
        return entry


class ClockOutForm(forms.ModelForm):
    class Meta:
        model = timepiece.Entry
        fields = ('location', 'comments')
        
    def __init__(self, *args, **kwargs):
        super(ClockOutForm, self).__init__(*args, **kwargs)
        self.fields['end_time'] = forms.DateTimeField(
            widget=forms.SplitDateTimeWidget(
                attrs={'class': 'timepiece-time'},
                date_format='%m/%d/%Y',
            ),
            initial=datetime.now(),
        )
        self.fields.keyOrder = ('location', 'end_time', 'comments')
    
    def save(self, commit=True):
        entry = super(ClockOutForm, self).save(commit=False)
        entry.end_time = self.cleaned_data['end_time']
        entry.unpause(date=self.cleaned_data['end_time'])
        if commit:
            entry.save()
        return entry


class AddUpdateEntryForm(forms.ModelForm):
    """
    This form will provide a way for users to add missed log entries and to
    update existing log entries.
    """

    start_time = forms.DateTimeField(
        widget=forms.SplitDateTimeWidget(
            attrs={'class': 'timepiece-time'},
            date_format='%m/%d/%Y',
        )
    )
    end_time = forms.DateTimeField(
        widget=forms.SplitDateTimeWidget(
            attrs={'class': 'timepiece-time'},
            date_format='%m/%d/%Y',
        )
    )
    
    class Meta:
        model = Entry
        exclude = ('user', 'pause_time', 'site', 'hours', 'status',)

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        super(AddUpdateEntryForm, self).__init__(*args, **kwargs)
        self.fields['project'].queryset = timepiece.Project.objects.filter(
            users=self.user,
        )
        if not self.instance.end_time:
            del self.fields['end_time']

    def clean_start_time(self):
        """
        Make sure that the start time is always before the end time
        """
        start = self.cleaned_data['start_time']

        try:
            end = self.cleaned_data['end_time']

            if start >= end:
                raise forms.ValidationError('The entry must start before it ends!')
        except KeyError:
            pass

        if start > datetime.now():
            raise forms.ValidationError('You cannot add entries in the future!')

        return start

    def clean_end_time(self):
        """
        Make sure no one tries to add entries that end in the future
        """
        try:
            start = self.cleaned_data['start_time']
        except KeyError:
            raise forms.ValidationError('Please enter a start time.')

        try:
            end = self.cleaned_data['end_time']
            if not end: raise Exception
        except:
            raise forms.ValidationError('Please enter an end time.')

        if start >= end:
            raise forms.ValidationError('The entry must start before it ends!')

        return end
    
    def save(self):
        instance = super(AddUpdateEntryForm, self).save(commit=False)
        instance.user = self.user
        instance.save()
        return instance
        
STATUS_CHOICES = [('','---------'),]
STATUS_CHOICES.extend(timepiece.ENTRY_STATUS)

class DateForm(forms.Form):
    from_date = forms.DateField(label="From", required=False)
    to_date = forms.DateField(label="To", required=False)
    status = forms.ChoiceField(choices=STATUS_CHOICES, widget=forms.HiddenInput(), required=False)
    activity = forms.ModelChoiceField(
        queryset=timepiece.Activity.objects.all(), 
        widget=forms.HiddenInput(), required=False,
    )
    def save(self):
        from_date = self.cleaned_data.get('from_date', '')
        to_date = self.cleaned_data.get('to_date', '')
        if to_date:
            to_date += timedelta(days=1)
        return (from_date, to_date)


class ProjectionForm(DateForm):
    user = forms.ModelChoiceField(queryset=None)
    
    def __init__(self, *args, **kwargs):
        users = kwargs.pop('users')
        super(ProjectionForm, self).__init__(*args, **kwargs)
        self.fields['user'].queryset = users


class BusinessForm(forms.ModelForm):
    class Meta:
        model = timepiece.Business
        fields = ('name', 'email', 'description', 'notes',)

class ProjectForm(forms.ModelForm):
    class Meta:
        model = timepiece.Project
        fields = (
            'name',
            'business',
            'trac_environment',
            'point_person',
            'type',
            'status',
            'description',
        )

    def __init__(self, *args, **kwargs):
        super(ProjectForm, self).__init__(*args, **kwargs)

    def save(self):
        instance = super(ProjectForm, self).save(commit=False)
        instance.save()
        return instance


class ProjectRelationshipForm(forms.ModelForm):
    class Meta:
        model = timepiece.ProjectRelationship
        fields = ('types',)

    def __init__(self, *args, **kwargs):
        super(ProjectRelationshipForm, self).__init__(*args, **kwargs)
        self.fields['types'].widget = forms.CheckboxSelectMultiple(
            choices=self.fields['types'].choices
        )
        self.fields['types'].help_text = ''


class RepeatPeriodForm(forms.ModelForm):
    class Meta:
        model = timepiece.RepeatPeriod
        fields = ('active', 'count', 'interval')

    def __init__(self, *args, **kwargs):
        super(RepeatPeriodForm, self).__init__(*args, **kwargs)
        self.fields['count'].required = False
        self.fields['interval'].required = False
        self.fields['date'] = forms.DateField(required=False)
    
    def _clean_optional(self, name):
        active = self.cleaned_data.get('active', False)
        value = self.cleaned_data.get(name, '')
        if active and not value:
            raise forms.ValidationError('This field is required.')
        return self.cleaned_data[name]
    
    def clean_count(self):
        return self._clean_optional('count')
    
    def clean_interval(self):
        return self._clean_optional('interval')
        
    def clean_date(self):
        active = self.cleaned_data.get('active', False)
        date = self.cleaned_data.get('date', '')
        if active and not self.instance.id and not date:
            raise forms.ValidationError('Start date is required for new billing periods')
        return date
    
    def clean(self):
        date = self.cleaned_data.get('date', '')
        if self.instance.id and date:
            latest = self.instance.billing_windows.latest()
            if self.cleaned_data['active'] and date < latest.end_date:
                raise forms.ValidationError('New start date must be after %s' % latest.end_date)
        return self.cleaned_data
    
    def save(self):
        period = super(RepeatPeriodForm, self).save(commit=False)
        if not self.instance.id and period.active:
            period.save()
            period.billing_windows.create(
                date=self.cleaned_data['date'],
                end_date=self.cleaned_data['date'] + period.delta(),
            )
        elif self.instance.id:
            period.save()
            start_date = self.cleaned_data['date']
            if period.active and start_date:
                latest = period.billing_windows.latest()
                if start_date > latest.end_date:
                    period.billing_windows.create(
                        date=latest.end_date,
                        end_date=start_date + period.delta(),
                    )
        period.update_billing_windows()
        return period


class PersonTimeSheet(forms.ModelForm):
    class Meta:
        model = timepiece.PersonRepeatPeriod
        fields = ('user',)
    
    def __init__(self, *args, **kwargs):
        super(PersonTimeSheet, self).__init__(*args, **kwargs)
        self.fields['user'].queryset = auth_models.User.objects.all().order_by('last_name')
