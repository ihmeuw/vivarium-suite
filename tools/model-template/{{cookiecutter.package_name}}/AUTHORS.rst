Authors
======

{% for author in cookiecutter.authors.split(',') %}
    -  {{- author -}}
{% endfor %}