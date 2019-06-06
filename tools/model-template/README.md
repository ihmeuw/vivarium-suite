vivarium_research_template
==============================

A minimal template for research repositories using ``vivarium``.

Usage
-----

    pip install cookiecutter
    git clone git@github.com:ihmeuw/vivarium_research_template.git
    cookiecutter vivarium_research_template/
    
Once your template repository has been created, most things will be filled in 
by the parameters you supply. 

**Things left to be manually filled out:**

- AUTHORS.rst
    
    Titles are provided, but the individuals assuming those titles for your 
    particular research project must be filled in manually.
    
- model_specifications/
    
    Both the model spec yaml and the branches yaml are barebones templates that
    will not run as-is without some manual adjustments custom to your project.

Maintenance
-----------

Things for the ``vivarium_developers`` to keep an eye on:

- model_specifications/{{cookiecutter.package_name}}.yaml
    
    Ensure the components and configuration keys supplied are kept up to date.
        
- setup.py
        
    Some packages (e.g., tables and numpy) are pinned because of requirements
    in the ``vivarium`` libraries. If these are removed from ``vivarium``,
    they should similarly be removed here.
