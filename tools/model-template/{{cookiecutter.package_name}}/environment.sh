#!/bin/bash

# Reset OPTIND so help can be invoked multiple times per shell session.
OPTIND=1
Help()
{ 
   # Display Help
   echo "Script to automatically create and validate conda environments."
   echo
   echo "Syntax: source environment.sh [-h|t|v]"
   echo "options:"
   echo "h     Print this Help."
   echo "t     Type of conda environment. Either 'simulation' (default) or 'artifact'."
   echo "f     Force creation of a new environment."
}

# Define variables
username=$(whoami)
env_type="simulation"
make_new="no"

# Process input options
while getopts ":hft:" option; do
   case $option in
      h) # display help
         Help
         return;;
      t) # Type of conda environment to build
         env_type=$OPTARG;;
      f) # Force creation of a new environment
         make_new="yes";;
     \?) # Invalid option
         echo "Error: Invalid option"
         return;;
   esac
done

# Parse environment name
env_name=$(basename "`pwd`")
env_name+="_$env_type"
branch_name=$(git rev-parse --abbrev-ref HEAD)
# Determine which requirements.txt to install from
if [ $env_type == 'simulation' ]; then
  install_file="requirements.txt"
elif [ $env_type == 'artifact' ]; then
  install_file="artifact_requirements.txt"
else
  echo "Invalid environment type. Valid argument types are 'simulation' and 'artifact'."
  return 
fi

# Pull repo to get latest changes from remote if remote exists
git ls-remote --exit-code --heads origin $branch_name >/dev/null 2>&1
exit_code=$?
if [[ $exit_code == '0' ]]; then
  git fetch --all
  echo "Git branch '$branch_name' exists in the remote repository, pulling latest changes..."
  git pull origin $branch_name
fi

# Check if environment exists already
create_env=$(conda info --envs | grep $env_name | head -n 1)
if [[ $create_env == '' ]]; then
  # No environment exists with this name
  echo "Environment $env_name does not exist."
  create_env="yes"
  env_exists="no"
elif [[ $make_new == 'yes' ]]; then
  # User has requested to make a new environment
  echo "Making a new environment."
  create_env="yes"
  env_exists="yes"
else
  env_exists="yes"
  conda activate $env_name
  # Check if existing environment needs to be recreated
  echo "Existing environment found for $env_name."
  one_week_ago=$(date -d "7 days ago" '+%Y-%m-%d %H:%M:%S')
  creation_time="$(head -n1 $CONDA_PREFIX/conda-meta/history)"
  creation_time=$(echo $creation_time | sed -e 's/^==>\ //g' -e 's/\ <==//g')
  requirements_modification_time="$(date -r $install_file '+%Y-%m-%d %H:%M:%S')"
  # Check if existing environment is older than a week or if environment was built 
  # before last modification to requirements file. If so, mark for recreation.
  if [[ $one_week_ago > $creation_time ]] | [[ $creation_time < $requirements_modification_time ]]; then
    echo "Environment is stale. Deleting and remaking environment..."
    create_env="yes"
  else
    # Install json parser if it is not installed
    jq_exists=$(conda list | grep -w jq)
    if [[ $jq_exists == '' ]]; then
      # Empty string is no return on grep
      conda install jq -c anaconda -y
    fi
    echo "Checking framework packages are up to date..."
    # Check if there has been an update to vivarium packages since last modification to requirements file
    # or more reccent than environment creation
    # Note: The lines we will return via grep will look like 'vivarium>=#.#.#' or will be of the format 
    # 'vivarium @ git+https://github.com/ihmeuw/vivarium@SOME_BRANCH'
    framework_packages=$(grep -E 'vivarium|gbd|risk_distribution|layered_config' $install_file)
    num_packages=$(grep -E 'vivarium|gbd|risk_distribution|layered_config' -c $install_file)
    
    # Iterate through each return of the grep output
    for ((i = 1; i <= $num_packages; i++)); do
      line=$(echo "$framework_packages" | sed -n "${i}p")
      # Check if the line contains '@'
      if [[ "$line" == *"@"* ]]; then
          repo_info=(${line//@/ })
          repo=${repo_info[0]}
          repo_branch=${repo_info[2]}
          last_update_time=$(curl -H "Accept: application/vnd.github.v3+json" https://api.github.com/repos/ihmeuw/$repo/commits?sha=$repo_branch | jq .[0].commit.committer.date)
      else
          repo=$(echo "$line" | cut -d '>' -f1)
          last_update_time=$(curl -s https://pypi.org/pypi/$repo/json | jq -r '.releases | to_entries | max_by(.key) | .value | .[0].upload_time')
      fi
      last_update_time=$(date -d "$last_update_time" '+%Y-%m-%d %H:%M:%S')
      if [[ $creation_time < $last_update_time ]]; then
        create_env="yes"
        echo "Last update time for $repo: $last_update_time. Environment is stale. Remaking environment..."
        break
      fi
    done
  fi
fi

if [[ $create_env == 'yes' ]]; then
  if [[ $env_exists == 'yes' ]]; then
    if [[ $env_name == $CONDA_DEFAULT_ENV ]]; then
      conda deactivate
    fi
    conda remove -n $env_name --all -y
  fi
  # Create conda environment
  conda create -n $env_name python=3.11 -c anaconda -y
  conda activate $env_name
  # NOTE: update branch name if you update requirements.txt in a branch
  echo "Installing packages for $env_type environment"
  pip install -r $install_file
  # Editable install of repo
  pip install -e .[dev] 
  # Install redis for simulation environments
  if [ $env_type == 'simulation' ]; then
    conda install redis -c anaconda -y
  fi
else
  echo "Existing environment validated"
fi
