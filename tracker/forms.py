from django import forms

from .models import Visit


class VisitForm(forms.ModelForm):
    class Meta:
        model = Visit
        fields = ["visit_date", "outdoor_unit_cleaned", "filter_replaced", "filter_size", "note", "paid"]
        widgets = {
            "visit_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"),
            "outdoor_unit_cleaned": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "filter_replaced": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "filter_size": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "20x25x1", "autocomplete": "off"}
            ),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "paid": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, property=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.property = property or (self.instance.property if self.instance.pk else None)

    def clean_visit_date(self):
        visit_date = self.cleaned_data["visit_date"]
        if self.instance.pk and self.property is not None:
            clash = (
                Visit.objects.filter(property=self.property, visit_date=visit_date)
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if clash:
                raise forms.ValidationError("This property already has a visit on that date.")
        return visit_date
