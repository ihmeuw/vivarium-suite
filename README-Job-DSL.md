# Jenkins Job DSL Setup for Multi-Multibranch Pipeline

## Overview

This setup creates a Job DSL script that generates multibranch pipelines automatically for each Jenkinsfile found in the monorepo.

## Setup Instructions

### 1. Create a Freestyle Job (Seed Job)

1. In Jenkins, create a new **Freestyle project**
2. Name it something like `seed` or `monorepo-seed`

### 2. Configure Source Code Management

1. Select **Git**
2. Repository URL: `https://github.com/ihmeuw/sim_sci_test_monorepo`
3. Credentials: Select your GitHub credentials
4. Branch Specifier: `*/main`

### 3. Add Build Step

1. Click **Add build step**
2. Select **Process Job DSLs**
3. Select **Use the provided DSL script**
4. In the DSL Script text area, you can either:
   - **Option A**: Copy the contents of `job-dsl-script.groovy`
   - **Option B**: Select "Look on Filesystem" and enter `job-dsl-script.groovy`

### 4. Configure DSL Processing

1. Action for removed jobs: **Delete**
2. Action for removed views: **Delete**
3. Check **Enable the groovy sandbox** (recommended for security)

### 5. Console Output

When you run the seed job, you should see output like:

```
=== Multi-Multibranch Pipeline DSL Script Starting ===
Job Name: seed
Repository URL: https://github.com/ihmeuw/sim_sci_test_monorepo
Repository Name: sim_sci_test_monorepo
Root Folder Path: Generated/sim_sci_test_monorepo
=== Step 1: Discovering Jenkinsfiles ===
Processing 2 Jenkinsfile(s):
  - libs/core/Jenkinsfile
  - libs/public_health/Jenkinsfile
=== Step 2: Creating Folder Structure ===
Created root folder: Generated/sim_sci_test_monorepo
Created subfolder: Generated/sim_sci_test_monorepo/libs
=== Step 3: Creating Multibranch Pipelines ===
Creating multibranch pipeline: Generated/sim_sci_test_monorepo/libs/core
✓ Created: Generated/sim_sci_test_monorepo/libs/core
Creating multibranch pipeline: Generated/sim_sci_test_monorepo/libs/public_health
✓ Created: Generated/sim_sci_test_monorepo/libs/public_health
=== Step 4: Summary ===
Successfully created:
- 1 root folder: Generated/sim_sci_test_monorepo
- 1 subfolders: libs
- 2 multibranch pipelines
=== Multi-Multibranch Pipeline DSL Script Complete ===
```

## Files

- `Jenkinsfile` - Contains Job DSL script (can be used directly)
- `job-dsl-script.groovy` - Standalone Job DSL script file
- `vars/monorepo.groovy` - Shared library (not used in Job DSL approach)
- `resources/multiPipelines.groovy` - Shared library resource (not used in Job DSL approach)

## Key Differences from Pipeline Approach

1. **No Shared Library**: Job DSL doesn't use Jenkins shared libraries
2. **Direct Job Creation**: Jobs are created immediately when the DSL script runs
3. **Console Output**: `println` statements appear in the build console
4. **Static Configuration**: No dynamic change detection - creates all pipelines

## Troubleshooting

### Console Output Not Appearing
- Make sure you're using `println` not `echo` in Job DSL scripts
- Verify the script is running in a Freestyle job with "Process Job DSLs" build step
- Check that the DSL script syntax is correct

### Permission Issues
- Ensure the Jenkins user has permissions to create jobs
- Check that the GitHub credentials are configured correctly
- Verify the repository URL is accessible

### Jobs Not Created
- Check the Jenkins console output for DSL processing results
- Look for any syntax errors in the DSL script
- Verify the folder structure matches your repository layout