#!/bin/bash

# Define variables
dependency_name=$1
branch_name=$2
workflow=$3
root_dir=$(pwd)
dependency_branch_name='main'

# Try multiple methods to get the branch name
if [ -n "$CHANGE_BRANCH" ]; then
  # Jenkins pull request build
  branch_name_to_check=$CHANGE_BRANCH
elif [ -n "$BRANCH_NAME" ]; then
  # Jenkins branch build
  branch_name_to_check=$BRANCH_NAME
elif [ -n "$GITHUB_HEAD_REF" ]; then
  # GitHub Actions pull request
  branch_name_to_check=$GITHUB_HEAD_REF
elif [ -n "$GITHUB_REF_NAME" ]; then
  # GitHub Actions branch build
  branch_name_to_check=$GITHUB_REF_NAME
else
  # Local development - try to get branch name from git
  branch_name_to_check=$(git symbolic-ref --short HEAD 2>/dev/null || git rev-parse --abbrev-ref HEAD)
    
  # If we're in detached HEAD state and can't determine branch, try to get it from the reflog
  if [ "$branch_name_to_check" = "HEAD" ]; then
    # Look for the last branch name in the reflog
    branch_name_to_check=$(git reflog -1 | grep -o 'from \S*' | cut -d' ' -f2 | sed 's/^origin\///')
        
    # If still nothing, fall back to main
    if [ -z "$branch_name_to_check" ]; then
      echo "Could not determine branch name, defaulting to main"
      branch_name_to_check="main"
      fi
  fi
fi

echo "Determined dependee branch name: ${branch_name_to_check}"
iterations=0

while [ "$branch_name_to_check" != "$dependency_branch_name" ] && [ $iterations -lt 20 ]
do
  echo "Checking for ${dependency_name} branch: '${branch_name_to_check}'"
  if
    git ls-remote --exit-code \
    --heads https://github.com/ihmeuw/"${dependency_name}".git "${branch_name_to_check}"
  then
    dependency_branch_name=${branch_name_to_check}
    echo "Found matching dependency branch: ${dependency_branch_name}"
  else
    echo "Could not find ${dependency_name} branch '${branch_name_to_check}'. Finding parent branch."
      
    # Try to find parent branch using git-merge-base and name-rev
    merge_base=$(git merge-base origin/main HEAD 2>/dev/null)
    if [ $? -eq 0 ] && [ -n "$merge_base" ]; then
      echo "Merge base: ${merge_base}"
      # Get the parent branch name, removing remotes/origin/ prefix and any ^0 suffix
      branch_name_to_check=$(git name-rev --exclude "tags/*" --refs="refs/remotes/origin/*" --name-only "$merge_base" | \
        sed -E 's/^(remotes\/)?origin\///' | \
        sed 's/\^[0-9]*$//')
      if [ "$branch_name_to_check" = "HEAD" ]; then
        branch_name_to_check="main"
      fi
    else
      # If merge-base fails, try to get parent from GitHub API if we have a PR number
      pr_number=$(echo "$branch_name_to_check" | grep -o 'PR-[0-9]*' | cut -d'-' -f2)
      if [ -n "$pr_number" ] && [ -n "$GITHUB_TOKEN" ]; then
        base_branch=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
          "https://api.github.com/repos/ihmeuw/$dependency_name/pulls/$pr_number" | \
          grep '"base": {' -A 3 | grep '"ref":' | cut -d'"' -f4)
          if [ -n "$base_branch" ]; then
            branch_name_to_check=$base_branch
          else
            branch_name_to_check="main"
          fi
      else
        branch_name_to_check="main"
      fi
    fi
      
    if [ -z "$branch_name_to_check" ] || [ "$branch_name_to_check" = "undefined" ]; then
      echo "Could not find parent branch. Will use released version of ${dependency_name}."
      branch_name_to_check="main"
    fi
    echo "Next dependee branch to check: ${branch_name_to_check}"
    iterations=$((iterations+1))
  fi
done

if [ "$workflow" = "github" ]; then
  echo "${dependency_name}_branch_name=${dependency_branch_name}" >> "$GITHUB_ENV"
fi

if [ "$dependency_branch_name" != "main" ]; then
  echo "Cloning ${dependency_name} branch: ${dependency_branch_name}"
  cd ..
  git clone --branch="${dependency_branch_name}" https://github.com/ihmeuw/"${dependency_name}".git
  cd "${dependency_name}" || exit
  pip install .
  cd "$root_dir" || exit
fi
