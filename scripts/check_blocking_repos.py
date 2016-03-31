#!/usr/bin/env python

import argparse

import rosdistro
from rosdistro.dependency_walker import DependencyWalker

def main():
    parser = argparse.ArgumentParser(
        description='Get unreleased repos and their dependencies.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '--rosdistro', metavar='ROS_DISTRO',
        help='The ROS distribution to check packages for')

    # If not specified, check for all repositories in the previous distribution
    parser.add_argument(
        '--repositories',
        metavar='REPOSITORY_NAME', nargs='*',
        help='Unreleased repositories to check dependencies for')

    parser.add_argument(
        '--depth',
        metavar='depth', type=int,
        help='Maxmium depth to crawl the dependency tree')

    args = parser.parse_args()

    distro_name = args.rosdistro
    repo_names = args.repositories
    depth = args.depth
    
    released_repos, blocked_repos, unblocked_blocking_repos, unblocked_unblocking_repos = \
        get_blocking_info(distro_name, repo_names, depth)

    show_released_repos(released_repos)
    show_blocked_repos(blocked_repos)
    show_unblocked_blocking_repos(unblocked_blocking_repos)
    show_unblocked_unblocking_repos(unblocked_unblocking_repos)
    
def is_released(repo, dist_file):
    return repo in dist_file.repositories and \
        dist_file.repositories[repo].release_repository is not None and \
        dist_file.repositories[repo].release_repository.version is not None

def get_blocking_info(distro_key, repo_names, depth):
    prev_distro_key = None

    index = rosdistro.get_index(rosdistro.get_index_url())
    valid_distro_keys = index.distributions.keys()
    valid_distro_keys.sort()
    if distro_key is None:
        distro_key = valid_distro_keys[-1]
    print('Checking packages for "%s" distribution' % distro_key)

    # Find the previous distribution to the current one
    try:
        i = valid_distro_keys.index(distro_key)
    except ValueError:
        print('Distribution key not found in list of valid distributions.')
        exit(-1)
    if i == 0:
        print('No previous distribution found.')
        exit(-1)
    prev_distro_key = valid_distro_keys[i - 1]

    cache = rosdistro.get_distribution_cache(index, distro_key)
    distro_file = cache.distribution_file

    prev_cache = rosdistro.get_distribution_cache(index, prev_distro_key)
    prev_distribution = rosdistro.get_cached_distribution(
        index, prev_distro_key, cache=prev_cache)

    prev_distro_file = prev_cache.distribution_file

    dependency_walker = DependencyWalker(prev_distribution)

    if repo_names is None:
        # Check missing dependencies for packages that were in the previous
        # distribution that have not yet been released in the current distribution
        # Filter repos without a version or a release repository
        keys = prev_distro_file.repositories.keys()
        prev_repo_names = set(
            repo for repo in keys if is_released(repo, prev_distro_file))
        repo_names = prev_repo_names
        ignored_inputs = []
    else:
        prev_repo_names = set(
            repo for repo in repo_names if is_released(repo, prev_distro_file))
        ignored_inputs = list(set(repo_names).difference(prev_repo_names))
        if len(ignored_inputs) > 0:
            print('Ignoring inputs for which repository info not found in previous distribution' +
                    ' (did you list a package instead of a repository?):')
            print('\n'.join(
                sorted('\t{0}'.format(repo) for repo in ignored_inputs)))

    keys = distro_file.repositories.keys()
    current_repo_names = set(
        repo for repo in keys if is_released(repo, distro_file))

    released_repos = prev_repo_names.intersection(
        current_repo_names)
    
    unreleased_repos = list(prev_repo_names.difference(
        current_repo_names))

    # Get a list of currently released packages
    current_package_names = set(
        pkg for repo in current_repo_names
        for pkg in distro_file.repositories[repo].release_repository.package_names)

    # Construct a dictionary where keys are repository names and values are a list
    # of the repos blocking/blocked by that repo
    blocked_repos = {}
    blocking_repos = {}
    unblocked_blocking_repos = set()

    if len(unreleased_repos) == 0:
        print('All inputs already released in {0}.'.format(
            distro_key))

    # Process repo dependencies
    unblocked_repos = set()
    total_blocking_repos = set()

    for repository_name in unreleased_repos:
        repo = prev_distro_file.repositories[repository_name]
        release_repo = repo.release_repository
        package_dependencies = set()
        packages = release_repo.package_names
        # Accumulate all dependencies for those packages
        for package in packages:
            recursive_dependencies = dependency_walker.get_recursive_depends(
                package, ['build', 'run', 'buildtool'], ros_packages_only=True,
                limit_depth=depth)
            package_dependencies = package_dependencies.union(
                recursive_dependencies)

        # For all package dependencies, check if they are released yet
        unreleased_pkgs = package_dependencies.difference(
            current_package_names)
        # remove the packages which this repo provides.
        unreleased_pkgs = unreleased_pkgs.difference(packages)
        # Now get the repositories for these packages.
        blocking_repos_for_this_repo = set(prev_distro_file.release_packages[pkg].repository_name
                            for pkg in unreleased_pkgs)
        if len(blocking_repos_for_this_repo) == 0:
            unblocked_repos.add(repository_name)
        else:
            # Get the repository for the unreleased packages
            blocked_repos[repository_name] = blocking_repos_for_this_repo
            total_blocking_repos |= blocking_repos_for_this_repo
            
            for blocking_repo in blocking_repos_for_this_repo:
                try: 
                    blocking_repos[blocking_repo] |= set([repository_name]) 
                except KeyError:
                    blocking_repos[blocking_repo] = set([repository_name])

    unblocked_blocking_repos_names = total_blocking_repos.intersection(unblocked_repos)
    unblocked_blocking_repos = {
        repo:blocking for repo, blocking in blocking_repos.iteritems() 
        if repo in unblocked_blocking_repos_names
        }
    unblocked_leaf_repos = unblocked_repos.difference(unblocked_blocking_repos_names)

    # Double-check repositories that we think are leaf repos
    for repo in unblocked_leaf_repos:
        # Check only one level of depends_on
        depends_on = dependency_walker.get_depends_on(package, 'build') | \
            dependency_walker.get_depends_on(package, 'run') | \
            dependency_walker.get_depends_on(package, 'buildtool')
        if len(depends_on) != 0:
            # There are packages that depend on this "leaf", but we didn't find
            # them initially because they weren't related to our inputs
            for package in depends_on:
                depends_on_repo = prev_distro_file.release_packages[package].repository_name
                try: 
                    unblocked_blocking_repos[repo] |= set([depends_on_repo]) 
                except KeyError:
                    unblocked_blocking_repos[repo] = set([depends_on_repo])

    unblocked_unblocking_repos = unblocked_leaf_repos.difference(
        unblocked_blocking_repos.keys())
    
    if not len(repo_names) == (len(ignored_inputs) + len(released_repos) + len(blocked_repos.keys()) + 
        len(unblocked_blocking_repos.keys()) + len(unblocked_unblocking_repos)):
        raise Exception('Somewhere a repo has not been accounted for')
    return released_repos, blocked_repos, unblocked_blocking_repos, unblocked_unblocking_repos

def show_released_repos(released_repos):
    if len(released_repos) > 0:
        print('The following repos have already been released:')
        print('\n'.join(
            sorted('\t{0}'.format(repo) for repo in released_repos)))

def show_blocked_repos(blocked_unreleased_repos):
    if len(blocked_unreleased_repos.keys()) > 0:
        print('\nThe following repos cannot be released because of unreleased '
            'dependencies:')
        for blocked_repo_name in sorted(blocked_unreleased_repos.keys()):
            unreleased_repos = blocked_unreleased_repos[blocked_repo_name]
            print('\t{0}:'.format(blocked_repo_name))

            print('\n'.join(
                sorted('\t\t{0}'.format(repo) for repo in unreleased_repos)))

def show_unblocked_blocking_repos(unblocked_blocking_repos):
    if len(unblocked_blocking_repos.keys()) > 0:
        print('\nThe following repos can be released, and are blocking other repos:')
        for blocking_repo_name in sorted(unblocked_blocking_repos.keys()):
            blocked_repos_by_this_repo = unblocked_blocking_repos[blocking_repo_name]
            print('\t{0}:'.format(blocking_repo_name))

            print('\n'.join(
                sorted('\t\t{0}'.format(repo) for repo in blocked_repos_by_this_repo)))

def show_unblocked_unblocking_repos(unblocked_unreleased_repos):
    if len(unblocked_unreleased_repos) > 0:
        print('\nThe following repos can be released, but do not block other repos:')
        print('\n'.join(
            sorted('\t{0}'.format(repo) for repo in unblocked_unreleased_repos)))

if __name__ == '__main__':
    main()
