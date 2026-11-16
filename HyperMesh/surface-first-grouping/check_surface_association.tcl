# Check that the element-to-surface association is complete and unambiguous
# for a set of surfaces.
#
# The procedure returns the number of elements in the union.  It raises an
# error when a surface has no associated elements, or when the per-surface
# counts sum to more than the union.  A larger sum means at least one element
# is claimed by two surfaces, so its group ownership is ambiguous.  If those
# surfaces belong to different groups, moving one group would remove the
# element from the other.
#
# The check does not account for elements that belong to no surface.  When the
# surface list is meant to cover the whole mesh, compare the returned union
# count with the total element count:
#
#   *createmark elems 1 "all"
#   set total [hm_marklength elems 1]
#   if {$union_count != $total} { error "elements outside the surface list" }
proc check_surface_association {surface_ids} {
    if {[llength $surface_ids] == 0} {
        error "surface_ids is empty"
    }

    set per_surface_sum 0
    foreach surface_id $surface_ids {
        *createmark elems 1 "by surface" $surface_id
        set count [hm_marklength elems 1]
        if {$count == 0} {
            error "surface $surface_id has no associated elements"
        }
        incr per_surface_sum $count
    }

    # Mark the union of all surfaces.  The mark deduplicates elements, so a
    # sum larger than the union reveals an element claimed by two surfaces.
    *createmark elems 1 "by surface" {*}$surface_ids
    set union_count [hm_marklength elems 1]

    if {$per_surface_sum != $union_count} {
        error "an element is claimed by more than one surface: per-surface sum $per_surface_sum, union $union_count"
    }

    return $union_count
}
