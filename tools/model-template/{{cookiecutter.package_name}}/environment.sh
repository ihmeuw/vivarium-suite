#!/bin/bash

# This script must be sourced, not executed, because it activates an environment
# in the current shell. Detect non-sourced invocation and fail with a clear message.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo
  echo "ERROR: This script must be sourced, not executed."
  echo "Usage: source environment.sh [options]"
  echo "Run 'source environment.sh -h' for help."
  exit 1
fi

# Define variables
username=$(whoami)
env_type="simulation"
make_new="no"
use_shared="no"
install_git_lfs="no"
days_until_stale=7 # Number of days until environment is considered stale

# Reset OPTIND so help can be invoked multiple times per shell session.
OPTIND=0
Help()
{ 
   # Display Help
   echo
   echo "Script to automatically create and validate conda environments."
   echo
   echo "Syntax: source environment.sh [-h|t|s|f|l]"
   echo "options:"
   echo "h     Print this Help."
   echo "t     Type of conda environment. Either 'simulation' (default) or 'artifact'."
   echo "s     Use shared environment (venv overlay). Recommended for cluster development."
   echo "f     Force creation of a new environment."
   echo "l     Install git lfs (only applies when creating a new conda environment)."
}

# Process input options
while getopts ":hsflt:" option; do
   case $option in
      h) # display help
         Help
         return;;
      t) # Type of conda environment to build
         env_type=$OPTARG;;
      s) # Use shared environment
         use_shared="yes";;
      f) # Force creation of a new environment
         make_new="yes";;
      l) # Install git lfs
         install_git_lfs="yes";;
     \?) # Invalid option
         echo
         echo "ERROR: Invalid option"
         return;;
   esac
done
# Parse environment name
env_name=$(basename "`pwd`")
env_name+="_$env_type"
branch_name=$(git rev-parse --abbrev-ref HEAD)

# Pull repo to get latest changes from remote if remote exists
git ls-remote --exit-code --heads origin $branch_name >/dev/null 2>&1
exit_code=$?
if [[ "$exit_code" == "0" ]]; then
  git fetch --all
  echo
  echo "Git branch '$branch_name' exists in the remote repository; pulling latest changes"
  git pull origin $branch_name
fi

# Capture error and exit script when sourced
# But also clear the trap when exiting to avoid affecting the parent shell
trap 'trap - ERR && return' ERR
set -E

if [[ "$use_shared" == "yes" ]]; then
  # Deactivate any active conda environments so only the venv is active
  for i in $(seq ${CONDA_SHLVL}); do
    conda deactivate
  done

  # Shared environment (venv overlay)
  venv_path=".venv/$env_name"
  
  if [[ ! -d "$venv_path" ]] || [[ "$make_new" == "yes" ]]; then
    # Venv doesn't exist or user requested force rebuild
    echo "Creating venv for shared environment in $venv_path"
    make build-shared-env type=$env_type force=yes
  fi
  echo "Activating shared environment venv $env_name"
  source ".venv/$env_name/bin/activate"

else
  # Initialize conda if not already initialized
  conda_path=$($SHELL -ic 'conda info --base')
  if [ ! -d "$conda_path" ]; then
    echo
    echo "ERROR: Conda path $conda_path does not exist"
    return
  fi
  if [ -f "$conda_path/etc/profile.d/conda.sh" ]; then
    echo
    echo "Initializing conda from $conda_path"
    source "$conda_path/etc/profile.d/conda.sh"
  else
    echo
    echo "ERROR: Unable to find conda in expected locations"
    return
  fi
  # Conda environment
  lfs_flag=""
  if [[ "$install_git_lfs" == "yes" ]]; then
    lfs_flag="lfs=yes"
  fi

  need_to_build="yes"
  env_info=$(conda info --envs | grep $env_name | head -n 1)
  
  if [[ "$env_info" != "" ]]; then
    # Environment exists
    if [[ "$make_new" != "yes" ]]; then
      # Not forcing rebuild, check if stale
      conda activate $env_name
      expiration_time=$(date -d "$days_until_stale days ago" +%s)
      creation_time="$(head -n1 $CONDA_PREFIX/conda-meta/history)"
      creation_time=$(echo $creation_time | sed -e 's/^==>\ //g' -e 's/\ <==//g')
      creation_time="$(date -d "$creation_time" +%s)"
      if [[ "$creation_time" -ge "$expiration_time" ]]; then
        # Not stale, skip building
        need_to_build="no"
      fi
    fi
  fi
  
  if [[ "$need_to_build" == "yes" ]]; then
    # Deactivate current environment if it's the one we're about to rebuild
    if [[ "$CONDA_DEFAULT_ENV" == "$env_name" ]]; then
      echo "Deactivating currently active environment..."
      conda deactivate
    fi
    echo "Creating conda environment '$env_name'"
    make build-env type=$env_type name=$env_name force=yes $lfs_flag
  fi
  echo "Activating conda environment '$env_name'"
  conda activate $env_name
fi

# Clear the ERR trap to avoid affecting subsequent commands in the parent shell
trap - ERR
