import java.nio.file.Path
import java.nio.file.Paths

/**
 * Get all ancestor folders paths.
 * <pre>
 *     ancestorFoldersPath(Paths.get('root/parent/child/file'))
 *     // returns [root, root/parent, root/parent/child]
 * </pre>
 * @param path A path.
 * @return All its ancestors paths.
 */
List<Path> ancestorFoldersPath(Path path) {
    if (path.parent == null) return []
    ancestorFoldersPath(path.parent) + [path.parent]
}

/**
 * Generate folders.
 * @param jenkinsfilePaths A list of Jenkinsfile paths.
 * @param rootFolder The root folder path under which all folders are created.
 */
def generateFolders(List<Path> jenkinsfilePaths, Path rootFolder) {
    jenkinsfilePaths
            .collect { ancestorFoldersPath(rootFolder.resolve(it).parent) }
            .flatten()
    // Remove duplicates in case some Jenkinsfiles share ancestors for optimization.
            .unique()
            .each {
                // Effectively provision the folder.
                folder(it.toString())
            }
}

/**
 * Generate Multibranch Pipelines.
 * @param jenkinsfilePaths A list of Jenkinsfile paths.
 * @param rootFolder The root folder path under which all pipelines are created.
 * @param repositoryURL The Git repository URL.
 */
def generateMultibranchPipelines(List<Path> jenkinsfilePaths, Path rootFolder, String repositoryURL) {
    // The following variables are needed to configure the branch source for GitHub.
    // Configuration for other version control providers vary.
    def matcher = repositoryURL =~ /.+[\/:](?<owner>[^\/]+)\/(?<repository>[^\/]+)$/
    if (!matcher.matches()) {
        throw new Exception("Could not parse repository URL: ${repositoryURL}")
    }
    String repositoryOwner = matcher.group('owner')
    String repositoryName = matcher.group('repository').replaceFirst(/\.git$/, '')

    // Discover branches strategies
    final int EXCLUDE_PULL_REQUESTS_STRATEGY_ID = 1

    // Discover pull requests from origin strategies
    final int USE_CURRENT_SOURCE_STRATEGY_ID = 2

    jenkinsfilePaths.each { jenkinsfilePath ->
        String pipelineName = jenkinsfilePath.parent

        multibranchPipelineJob(rootFolder.resolve(pipelineName).toString()) {
            branchSources {
                branchSource {
                    source {
                        github {
                            // We must set a branch source ID.
                            id('github')

                            // repoOwner, repository, repositoryUrl and configuredByUrl are all required
                            repoOwner(repositoryOwner)
                            repository(repositoryName)
                            repositoryUrl(repositoryURL)
                            configuredByUrl(false)

                            // Jenkins credential ID for the GitHub App. Passed in by
                            // monorepo.groovy so different Jenkins instances or orgs
                            // (e.g. ihmeuw vs. a future private fork) can supply their own.
                            // Note that this is just a pointer to a secret stored in Jenkins,
                            // not the credential itself.
                            credentialsId(githubCredentialsId)

                            traits {
                                // Discover branches but exclude those that are also PRs
                                gitHubBranchDiscovery {
                                    strategyId(EXCLUDE_PULL_REQUESTS_STRATEGY_ID)
                                }
                                // Discover pull requests from origin
                                gitHubPullRequestDiscovery {
                                    strategyId(USE_CURRENT_SOURCE_STRATEGY_ID)
                                }

                                // By default, Jenkins notifies GitHub with a constant context, i.e. a string that
                                // identifies the check. We want each individual build result to have its own context so
                                // they do not conflict. Requires the github-scm-trait-notification-context-plugin to be
                                // installed on the Jenkins instance.
                                notificationContextTrait {
                                    contextLabel("continuous-integration/jenkins/$pipelineName")
                                    typeSuffix(false)
                                }
                            }
                        }
                    }

                    // By default, Jenkins will trigger builds as it detects changes on the source repository. We want
                    // to avoid that since we will trigger child pipelines on our own only when relevant.
                    strategy {
                        defaultBranchPropertyStrategy {
                            props {
                                noTriggerBranchProperty()
                            }
                        }
                    }
                }
            }
            factory {
                workflowBranchProjectFactory {
                    scriptPath(jenkinsfilePath.toString())
                }
            }
            orphanedItemStrategy {
                discardOldItems {
                    // Keeping pipelines a few days for branches that do not exist anymore can be useful for
                    // troubleshooting purposes.
                    daysToKeep(3)
                }
            }
            triggers {
                periodicFolderTrigger {
                    // Scan branches once a week at least to remove orphan pipelines.
                    interval('7d')
                }
            }
        }
    }
}

// `jenkinsfilePathStrings`, `rootFolderStr`, `repositoryURL`, and `githubCredentialsId`
// are global variables injected via `jobDsl`'s `additionalParameters` option in monorepo.groovy.
List<Path> jenkinsfilePaths = jenkinsfilePathStrings.collect { Paths.get(it) }
Path rootFolder = Paths.get(rootFolderStr)

generateFolders(jenkinsfilePaths, rootFolder)
generateMultibranchPipelines(jenkinsfilePaths, rootFolder, repositoryURL)
