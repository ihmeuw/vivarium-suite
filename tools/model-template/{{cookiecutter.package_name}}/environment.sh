#!/bin/bash

set -e # exit on error

# Initialize conda if not already initialized
for conda_path in "$HOME/miniconda3" "$HOME/anaconda3" "/opt/conda" "/usr/local/miniconda3" "/usr/local/anaconda3"; do
  if [ -f "$conda_path/etc/profile.d/conda.sh" ]; then
    echo
    echo "Initializing conda from $conda_path"
    source "$conda_path/etc/profile.d/conda.sh"
    break
  else
    echo
    echo "ERROR: Unable to find conda in expected locations"
    exit 1
  fi
done

# Reset OPTIND so help can be invoked multiple times per shell session.
OPTIND=1
Help()
{ 
   # Display Help
   echo
   echo "Script to automatically create and validate conda environments."
   echo
   echo "Syntax: source environment.sh [-h|t|v]"
   echo "options:"
   echo "h     Print this Help."
   echo "t     Type of conda environment. Either 'simulation' (default) or 'artifact'."
   echo "f     Force creation of a new environment."
   echo "l     Install git lfs."
}

# Define variables
username=$(whoami)
env_type="simulation"
make_new="no"
install_git_lfs="no"
days_until_stale=7 # Number of days until environment is considered stale


# Process input options
while getopts ":hflt:" option; do
   case $option in
      h) # display help
         Help
         exit 0;;
      t) # Type of conda environment to build
         env_type=$OPTARG;;
      f) # Force creation of a new environment
         make_new="yes";;
      l) # Install git lfs
         install_git_lfs="yes";;
     \?) # Invalid option
         echo
         echo "ERROR: Invalid option"
         exit 1;;
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
  echo
  echo "Invalid environment type. Valid argument types are 'simulation' and 'artifact'."
  exit 1 
fi

# Pull repo to get latest changes from remote if remote exists
set +e # Do not exit on error for git ls-remote
git ls-remote --exit-code --heads origin $branch_name >/dev/null 2>&1
exit_code=$?
set -e # Re-enable exit on error
if [[ $exit_code == '0' ]]; then
  git fetch --all
  echo
  echo "Git branch '$branch_name' exists in the remote repository; pulling latest changes"
  git pull origin $branch_name
fi

# Check if environment exists already
env_info=$(conda info --envs | grep $env_name | head -n 1)
if [[ $env_info == '' ]]; then
  # No environment exists with this name
  echo
  echo "Environment $env_name does not exist"
  create_env="yes"
  env_exists="no"
elif [[ $make_new == 'yes' ]]; then
  # User has requested to make a new environment
  echo
  echo "Making a new environment"
  create_env="yes"
  env_exists="yes"
else
  env_exists="yes"
  conda activate $env_name
  # Check if existing environment needs to be recreated
  echo
  echo "Existing environment found for $env_name"
  expiration_time=$(date -d "$days_until_stale days ago" +%s)
  creation_time="$(head -n1 $CONDA_PREFIX/conda-meta/history)"
  creation_time=$(echo $creation_time | sed -e 's/^==>\ //g' -e 's/\ <==//g')
  creation_time="$(date -d "$creation_time" +%s)"
  requirements_modification_time="$(date -r $install_file +%s)"
  # Check if existing environment is older than a week or if environment was built 
  # before last modification to requirements file. If so, mark for recreation.
  if [[ $creation_time < $expiration_time ]] || [[ $creation_time < $requirements_modification_time ]]; then
    echo
    echo "Environment is stale; deleting and remaking"
    create_env="yes"
  else
    echo
    echo "Environment is up to date; no action needed"

  ##############################################################################
  # FIXME [MIC-6259]
  #   This if/else block has never worked correctly due to using a single |
  #   instead of double || (or) in the above 'if' check.
  #   As such, we are commenting out the following complicated logic to not drastically
  #   change behavior, but this needs to all be fixed.
  ##############################################################################

  # else
  #   # Install json parser if it is not installed
  #   jq_exists=$(conda list | grep -w jq)
  #   if [[ $jq_exists == '' ]]; then
  #     # Empty string is no return on grep
  #     conda install jq -c anaconda -y
  #   fi
  #   echo "Checking framework packages are up to date..."
  #   # Check if there has been an update to vivarium packages since last modification to requirements file
  #   # or more reccent than environment creation
  #   # Note: The lines we will return via grep will look like 'vivarium>=#.#.#' or will be of the format 
  #   # 'vivarium @ git+https://github.com/ihmeuw/vivarium@SOME_BRANCH'
  #   framework_packages=$(grep -E 'vivarium|gbd|risk_distribution|layered_config' $install_file)
  #   num_packages=$(grep -E 'vivarium|gbd|risk_distribution|layered_config' -c $install_file)
    
  #   # Iterate through each return of the grep output
  #   for ((i = 1; i <= $num_packages; i++)); do
  #     line=$(echo "$framework_packages" | sed -n "${i}p")
  #     # Check if the line contains '@'
  #     if [[ "$line" == *"@"* ]]; then
  #         repo_info=(${line//@/ })
  #         repo=${repo_info[0]}
  #         repo_branch=${repo_info[2]}
  #         last_update_time=$(curl -H "Accept: application/vnd.github.v3+json" https://api.github.com/repos/ihmeuw/$repo/commits?sha=$repo_branch | jq .[0].commit.committer.date)
  #     else
  #         repo=$(echo "$line" | cut -d '>' -f1)
  #         last_update_time=$(curl -s https://pypi.org/pypi/$repo/json | jq -r '.releases | to_entries | max_by(.key) | .value | .[0].upload_time')
  #     fi
  #     last_update_time=$(date -d "$last_update_time" '+%Y-%m-%d %H:%M:%S')
  #     if [[ $creation_time < $last_update_time ]]; then
  #       create_env="yes"
  #       echo "Last update time for $repo: $last_update_time. Environment is stale. Remaking environment..."
  #       break
  #     fi
  #   done
  fi
fi

if [[ $create_env == 'yes' ]]; then
  if [[ $env_exists == 'yes' ]]; then
    if [[ $env_name == $CONDA_DEFAULT_ENV ]]; then
      conda deactivate
    fi
    echo
    echo "Removing existing environment $env_name"
    conda remove -n $env_name --all -y
  fi
  # Create conda environment
  echo
  echo "Creating new conda environment $env_name"
  conda create -n $env_name python=3.11 -c anaconda -y
  conda activate $env_name
  # NOTE: update branch name if you update requirements.txt in a branch
  echo
  echo "Installing packages for $env_type environment"
  pip install uv
  artifactory_url="https://artifactory.ihme.washington.edu/artifactory/api/pypi/pypi-shared/simple"
  uv pip install -r $install_file --extra-index-url $artifactory_url --index-strategy unsafe-best-match
  # Editable install of repo
  uv pip install -e .[dev] --extra-index-url $artifactory_url --index-strategy unsafe-best-match
  # Install redis for simulation environments
  if [ $env_type == 'simulation' ]; then
    conda install redis -c anaconda -y
  fi
  # Install git lfs if requested
  if [ $install_git_lfs == 'yes' ]; then
    git lfs install
  fi
else
  echo
  echo "Existing environment validated"
fi

echo
echo "*** FINISHED ***"
echo
echo "Don't forget to activate the environment:"
echo "conda activate $env_name"
