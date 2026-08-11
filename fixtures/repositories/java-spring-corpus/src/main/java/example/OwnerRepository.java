package example;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface OwnerRepository extends JpaRepository<Owner, Integer> {
    String BASE_QUERY = "SELECT o FROM Owner o WHERE o.lastName = ?1";

    @Query("SELECT o FROM Owner o WHERE o.lastName LIKE ?1%")
    List<Owner> findLiteral(String lastName);

    @Query(BASE_QUERY)
    List<Owner> findDynamic(String lastName);
}
