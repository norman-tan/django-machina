# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import datetime

from django.db.models import F
from django.db.models import Q

from machina.core.db.models import get_model
from machina.core.loading import get_class

Forum = get_model('forum', 'Forum')
ForumReadTrack = get_model('forum_tracking', 'ForumReadTrack')
TopicReadTrack = get_model('forum_tracking', 'TopicReadTrack')
Post = get_model('forum_conversation', 'Post')

PermissionHandler = get_class('forum_permission.handler', 'PermissionHandler')

class TrackingHandler(object):
    """
    The TrackingHandler allows to filter list of forums and list of topics
    in order to get only the forums which contain unread topics or the unread
    topics.
    """
    def __init__(self, request=None):
        self.request = request
        self.perm_handler = request.forum_permission_handler if request \
            else PermissionHandler()

    def get_unread_forums(self, user):
        """
        Returns the list of unread forums for the given user.
        """
        unread_forums = []

        # A user which is not authenticated will never see a forum as unread
        if not user.is_authenticated():
            return unread_forums

        readable_forums = self.perm_handler.forum_list_filter(Forum.objects.all(), user)
        unread = ForumReadTrack.objects.get_unread_forums_from_list(readable_forums, user)
        unread_forums.extend(unread)

        return unread_forums

    def get_unread_topics(self, topics, user):
        """
        Returns a list of unread topics for the given user from a given
        set of topics.
        """

        startTime1 = datetime.datetime.now()

        unread_topics = []

        # A user which is not authenticated will never see a topic as unread.
        # If there are no topics to consider, we stop here.
        if not user.is_authenticated() or topics is None or not len(topics):
            return unread_topics

        startTime = datetime.datetime.now()

        # A topic can be unread if a track for itself exists with a mark time that
        # is less important than its update date.
        topic_ids = [topic.id for topic in topics]
        topic_tracks = TopicReadTrack.objects.filter(topic__in=topic_ids, user=user)
        tracked_topics = dict(topic_tracks.values_list('topic__pk', 'mark_time'))

        print("(%s) get tracked topics (TrackingHandler.get_unread_topics)" % (datetime.datetime.now() - startTime))

        startTime = datetime.datetime.now()

        if tracked_topics:
            for topic in topics:
                topic_last_modification_date = topic.last_post_on or topic.created
                if topic.id in tracked_topics.keys() \
                        and topic_last_modification_date > tracked_topics[topic.id]:
                    unread_topics.append(topic)

                startTime = datetime.datetime.now()

        print("(%s) get unread topic from tracked topics (TrackingHandler.get_unread_topics)" % (datetime.datetime.now() - startTime))

        startTime = datetime.datetime.now()

        # A topic can be unread if a track for its associated forum exists with
        # a mark time that is less important than its creation or update date.
        forum_ids = [topic.forum_id for topic in topics]
        forum_tracks = ForumReadTrack.objects.filter(forum_id__in=forum_ids, user=user)
        tracked_forums = dict(forum_tracks.values_list('forum__pk', 'mark_time'))

        print("(%s) get tracked forums (TrackingHandler.get_unread_topics)" % (datetime.datetime.now() - startTime))

        startTime = datetime.datetime.now()

        if tracked_forums:
            for topic in topics:
                topic_last_modification_date = topic.last_post_on or topic.created
                if ((topic.forum_id in tracked_forums.keys() and topic.id not in tracked_topics) and
                        topic_last_modification_date > tracked_forums[topic.forum_id]):
                    unread_topics.append(topic)

        print("(%s) get unread topics from tracked forums (TrackingHandler.get_unread_topics)" % (datetime.datetime.now() - startTime))

        startTime = datetime.datetime.now()

        # A topic can be unread if no tracks exists for it
        for topic in topics:
            if topic.forum_id not in tracked_forums and topic.id not in tracked_topics:
                unread_topics.append(topic)

        print("(%s) get untracked topics (TrackingHandler.get_unread_topics)" % (datetime.datetime.now() - startTime))

        print("*** (%s) TOTOAL (TrackingHandler.get_unread_topics)" % (datetime.datetime.now() - startTime1))

        return list(set(unread_topics))

    def get_oldest_unread_post(self, topic, user):

        if not user.is_authenticated() or topic is None:
            return None

        mark_time = None

        topic_tracks = TopicReadTrack.objects.filter(topic=topic.id, user=user).order_by('mark_time')[:1]
        forum_tracks = ForumReadTrack.objects.filter(forum=topic.forum.id, user=user).order_by('mark_time')[:1]

        # A track for this topic exists. Any post newer than the tracked mark time is unread
        if topic_tracks:
            mark_time = topic_tracks.first().mark_time

        # A track for the forum exists. If its mark time is older than that of the topic track,
        # use the oldest one. Or if there is no topic track, use the forum track mark time.
        if forum_tracks:
            if (mark_time and forum_tracks.first().mark_time < mark_time) or not mark_time:
                mark_time = forum_tracks.first().mark_time

        # Get the oldest post with a date after the mark time
        if topic_tracks or forum_tracks:
            unread_posts = Post.objects.filter(topic=topic, created__gt=mark_time).order_by('created')[:1]
            for post in  unread_posts:
                mark_time = post.pk

        return mark_time

    def mark_forums_read(self, forums, user):
        """
        Marks a list of forums as read.
        """
        if not forums or not user.is_authenticated():
            return

        forums = sorted(forums, key=lambda f: f.level)

        # Update all forum tracks to the current date for the considered forums
        for forum in forums:
            forum_track = ForumReadTrack.objects.get_or_create(forum=forum, user=user)[0]
            forum_track.save()
        # Delete all the unnecessary topic tracks
        TopicReadTrack.objects.filter(topic__forum__in=forums, user=user).delete()
        # Update parent forum tracks
        self._update_parent_forum_tracks(forums[0], user)

    def mark_topic_read(self, topic, user):
        """
        Marks a topic as read.
        """
        if not user.is_authenticated():
            return

        forum = topic.forum
        try:
            forum_track = ForumReadTrack.objects.get(forum=forum, user=user)
        except ForumReadTrack.DoesNotExist:
            forum_track = None

        if forum_track is None \
                or (topic.last_post_on and forum_track.mark_time < topic.last_post_on):
            topic_track, created = TopicReadTrack.objects.get_or_create(topic=topic, user=user)
            if not created:
                topic_track.save()  # mark_time filled

            # If no other topic is unread inside the considered forum, the latter should also be
            # marked as read.
            unread_topics = forum.topics.filter(
                Q(tracks__user=user, tracks__mark_time__lt=F('last_post_on')) |
                Q(forum__tracks__user=user, forum__tracks__mark_time__lt=F('last_post_on'),
                  tracks__isnull=True)).exclude(id=topic.id)

            forum_topic_tracks = TopicReadTrack.objects.filter(topic__forum=forum, user=user)
            if not unread_topics.exists() and (
                    forum_track is not None or
                    forum_topic_tracks.count() == forum.topics.filter(approved=True).count()):
                # The topics that are marked as read inside the forum for the given user will be
                # deleted while the forum track associated with the user must be created or updated.
                # This is done only if there are as many topic tracks as approved topics in case
                # the related forum has not beem previously marked as read.
                TopicReadTrack.objects.filter(topic__forum=forum, user=user).delete()
                forum_track, _ = ForumReadTrack.objects.get_or_create(forum=forum, user=user)
                forum_track.save()

                # Update parent forum tracks
                self._update_parent_forum_tracks(forum, user)

    def _update_parent_forum_tracks(self, forum, user):
        for forum in forum.get_ancestors(ascending=True):
            # If no other topics are unread inside the considered forum, the latter should also
            # be marked as read.
            unread_topics = forum.topics.filter(
                Q(tracks__user=user, tracks__mark_time__lt=F('last_post_on')) |
                Q(forum__tracks__user=user, forum__tracks__mark_time__lt=F('last_post_on'),
                  tracks__isnull=True))
            if unread_topics.exists():
                break

            # The topics that are marked as read inside the forum for the given user
            # wil be deleted while the forum track associated with the user must be
            # created or updated.
            TopicReadTrack.objects.filter(topic__forum=forum, user=user).delete()
            forum_track, _ = ForumReadTrack.objects.get_or_create(forum=forum, user=user)
            forum_track.save()
